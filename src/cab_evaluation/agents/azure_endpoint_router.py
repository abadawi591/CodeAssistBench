"""
Azure OpenAI Endpoint Router with Load Balancing and Failover.

This module provides intelligent routing across multiple Azure OpenAI endpoints
with automatic failover, weighted load balancing, and retry logic using tenacity.

Features:
- Priority-based routing with weighted distribution
- Automatic failover on endpoint failures
- Rate limit awareness and backoff
- Health tracking per endpoint
- Async-first design for high concurrency
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum
import random

from openai import AsyncAzureOpenAI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
    RetryError
)

logger = logging.getLogger(__name__)


class EndpointStatus(Enum):
    """Health status of an endpoint."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # Experiencing errors but still usable
    UNHEALTHY = "unhealthy"  # Temporarily unavailable
    RATE_LIMITED = "rate_limited"  # Hit rate limit, back off


@dataclass
class AzureEndpoint:
    """Configuration for a single Azure OpenAI endpoint."""
    name: str
    endpoint_url: str
    deployment_name: str
    keyvault_secret: str
    rpm_limit: int
    tpm_limit: int
    priority: int  # Lower = higher priority
    
    # Runtime state (not part of config)
    status: EndpointStatus = field(default=EndpointStatus.HEALTHY)
    consecutive_failures: int = field(default=0)
    last_failure_time: float = field(default=0.0)
    requests_this_minute: int = field(default=0)
    minute_start_time: float = field(default_factory=time.time)
    
    # Computed weight based on capacity
    @property
    def weight(self) -> float:
        """Calculate routing weight based on RPM capacity and priority."""
        if self.status == EndpointStatus.UNHEALTHY:
            return 0.0
        if self.status == EndpointStatus.RATE_LIMITED:
            return 0.1  # Small chance to retry
        
        # Base weight from RPM capacity
        base_weight = self.rpm_limit / 1000  # Normalize
        
        # Adjust by priority (priority 1 = 1.0x, priority 4 = 0.7x)
        priority_factor = 1.0 - (self.priority - 1) * 0.1
        
        # Reduce weight if degraded
        if self.status == EndpointStatus.DEGRADED:
            priority_factor *= 0.5
            
        return base_weight * priority_factor


# Default endpoint configurations
DEFAULT_ENDPOINTS = [
    AzureEndpoint(
        name="eastus2-primary",
        endpoint_url="https://deepprompteastus2.openai.azure.com",
        deployment_name="gpt-5.2",
        keyvault_secret="gpt-5-2-api-key",
        rpm_limit=10000,
        tpm_limit=1000000,
        priority=1
    ),
    AzureEndpoint(
        name="southcentralus",
        endpoint_url="https://deeppromptsouthcentralus.openai.azure.com",
        deployment_name="gpt-5.2_2",
        keyvault_secret="gpt-5-2-number-2-api-key",
        rpm_limit=10000,
        tpm_limit=1000000,
        priority=2
    ),
    AzureEndpoint(
        name="swedencentral",
        endpoint_url="https://deeppromptswedencentral.openai.azure.com",
        deployment_name="gpt-5.2",
        keyvault_secret="gpt-5-2-number-3-api-key",
        rpm_limit=8500,
        tpm_limit=850000,
        priority=3
    ),
    AzureEndpoint(
        name="eastus2-secondary",
        endpoint_url="https://deepprompteastus2.openai.azure.com",
        deployment_name="gpt-5.2-4",
        keyvault_secret="gpt-5-2-number-4-api-key",
        rpm_limit=2000,
        tpm_limit=200000,
        priority=4
    ),
]


class AzureEndpointRouter:
    """
    Intelligent router for multiple Azure OpenAI endpoints.
    
    Handles load balancing, failover, and retry logic across endpoints.
    """
    
    # Key Vault URL for all secrets
    KEYVAULT_URL = "https://abadawikeys.vault.azure.net"
    
    # Health recovery settings
    FAILURE_THRESHOLD = 3  # Consecutive failures before marking unhealthy
    RECOVERY_TIME_SECONDS = 60  # Time before retrying unhealthy endpoint
    RATE_LIMIT_BACKOFF_SECONDS = 30  # Time to back off after rate limit
    
    def __init__(
        self,
        endpoints: Optional[List[AzureEndpoint]] = None,
        api_version: str = "2024-12-01-preview"
    ):
        """
        Initialize the endpoint router.
        
        Args:
            endpoints: List of endpoint configurations (defaults to DEFAULT_ENDPOINTS)
            api_version: Azure OpenAI API version
        """
        self.endpoints = endpoints or [AzureEndpoint(**e.__dict__) for e in DEFAULT_ENDPOINTS]
        self.api_version = api_version
        
        # Client cache: endpoint_name -> AsyncAzureOpenAI
        self._clients: Dict[str, AsyncAzureOpenAI] = {}
        
        # API key cache: secret_name -> key
        self._api_keys: Dict[str, str] = {}
        
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
        
        # Stats tracking
        self._total_requests = 0
        self._successful_requests = 0
        self._failed_requests = 0
        self._failovers = 0
        
        logger.info(f"Initialized Azure Endpoint Router with {len(self.endpoints)} endpoints")
        logger.info(f"Total capacity: {sum(e.rpm_limit for e in self.endpoints):,} RPM, "
                   f"{sum(e.tpm_limit for e in self.endpoints):,} TPM")
    
    def _get_api_key_from_keyvault(self, secret_name: str) -> str:
        """Retrieve API key from Azure Key Vault with caching."""
        if secret_name in self._api_keys:
            return self._api_keys[secret_name]
        
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
            
            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=self.KEYVAULT_URL, credential=credential)
            secret = client.get_secret(secret_name)
            
            self._api_keys[secret_name] = secret.value
            logger.info(f"Retrieved API key from Key Vault: {secret_name}")
            return secret.value
            
        except Exception as e:
            logger.error(f"Failed to retrieve secret '{secret_name}' from Key Vault: {e}")
            raise
    
    def _get_client(self, endpoint: AzureEndpoint) -> AsyncAzureOpenAI:
        """Get or create an async client for the endpoint."""
        cache_key = f"{endpoint.name}_{endpoint.deployment_name}"
        
        if cache_key not in self._clients:
            api_key = self._get_api_key_from_keyvault(endpoint.keyvault_secret)
            
            self._clients[cache_key] = AsyncAzureOpenAI(
                api_key=api_key,
                api_version=self.api_version,
                azure_endpoint=endpoint.endpoint_url
            )
            logger.info(f"Created client for endpoint: {endpoint.name}")
        
        return self._clients[cache_key]
    
    def _select_endpoint(self) -> AzureEndpoint:
        """
        Select the best endpoint using weighted random selection.
        
        Returns:
            Selected endpoint based on weight and availability
        """
        # Update endpoint statuses
        current_time = time.time()
        
        for endpoint in self.endpoints:
            # Check if rate-limited endpoint can be retried
            if endpoint.status == EndpointStatus.RATE_LIMITED:
                if current_time - endpoint.last_failure_time > self.RATE_LIMIT_BACKOFF_SECONDS:
                    endpoint.status = EndpointStatus.HEALTHY
                    logger.info(f"Endpoint {endpoint.name} recovered from rate limit")
            
            # Check if unhealthy endpoint can be retried
            if endpoint.status == EndpointStatus.UNHEALTHY:
                if current_time - endpoint.last_failure_time > self.RECOVERY_TIME_SECONDS:
                    endpoint.status = EndpointStatus.DEGRADED  # Try cautiously
                    endpoint.consecutive_failures = 0
                    logger.info(f"Endpoint {endpoint.name} attempting recovery")
            
            # Reset request counter if minute has passed
            if current_time - endpoint.minute_start_time > 60:
                endpoint.requests_this_minute = 0
                endpoint.minute_start_time = current_time
        
        # Filter available endpoints
        available = [e for e in self.endpoints if e.weight > 0]
        
        if not available:
            # All endpoints down - force retry on highest priority
            logger.warning("All endpoints unavailable, forcing retry on primary")
            return self.endpoints[0]
        
        # Weighted random selection
        total_weight = sum(e.weight for e in available)
        r = random.random() * total_weight
        
        cumulative = 0
        for endpoint in available:
            cumulative += endpoint.weight
            if r <= cumulative:
                return endpoint
        
        return available[0]
    
    def _handle_success(self, endpoint: AzureEndpoint):
        """Update endpoint state after successful request."""
        endpoint.consecutive_failures = 0
        endpoint.requests_this_minute += 1
        
        if endpoint.status == EndpointStatus.DEGRADED:
            endpoint.status = EndpointStatus.HEALTHY
            logger.info(f"Endpoint {endpoint.name} recovered to healthy")
        
        self._successful_requests += 1
    
    def _handle_failure(self, endpoint: AzureEndpoint, error: Exception):
        """Update endpoint state after failed request."""
        endpoint.consecutive_failures += 1
        endpoint.last_failure_time = time.time()
        
        error_str = str(error).lower()
        
        # Check for rate limiting
        if "rate" in error_str and "limit" in error_str:
            endpoint.status = EndpointStatus.RATE_LIMITED
            logger.warning(f"Endpoint {endpoint.name} rate limited")
        elif endpoint.consecutive_failures >= self.FAILURE_THRESHOLD:
            endpoint.status = EndpointStatus.UNHEALTHY
            logger.warning(f"Endpoint {endpoint.name} marked unhealthy after {endpoint.consecutive_failures} failures")
        else:
            endpoint.status = EndpointStatus.DEGRADED
        
        self._failed_requests += 1
    
    async def call_with_failover(
        self,
        messages: List[Dict[str, str]],
        max_completion_tokens: int = 4096,
        temperature: float = 0.1,
        max_attempts: int = 4,  # Try all 4 endpoints
        **kwargs
    ) -> str:
        """
        Make an API call with automatic failover across endpoints.
        
        Args:
            messages: Chat messages
            max_completion_tokens: Max tokens in response
            temperature: Sampling temperature
            max_attempts: Maximum number of endpoints to try
            **kwargs: Additional arguments for the API call
            
        Returns:
            Model response text
            
        Raises:
            Exception: If all endpoints fail
        """
        self._total_requests += 1
        
        tried_endpoints = set()
        last_error = None
        
        for attempt in range(max_attempts):
            # Select endpoint (avoiding already-tried ones if possible)
            endpoint = self._select_endpoint()
            
            # If we've tried this endpoint, find another
            attempts_to_find_new = 0
            while endpoint.name in tried_endpoints and attempts_to_find_new < 10:
                endpoint = self._select_endpoint()
                attempts_to_find_new += 1
            
            tried_endpoints.add(endpoint.name)
            
            try:
                client = self._get_client(endpoint)
                
                logger.debug(f"Attempting request on {endpoint.name} (attempt {attempt + 1}/{max_attempts})")
                
                response = await client.chat.completions.create(
                    model=endpoint.deployment_name,
                    messages=messages,
                    max_completion_tokens=max_completion_tokens,
                    temperature=temperature,
                    **kwargs
                )
                
                self._handle_success(endpoint)
                return response.choices[0].message.content
                
            except Exception as e:
                last_error = e
                self._handle_failure(endpoint, e)
                logger.warning(f"Endpoint {endpoint.name} failed: {e}")
                
                if attempt < max_attempts - 1:
                    self._failovers += 1
                    logger.info(f"Failing over to another endpoint (attempt {attempt + 2}/{max_attempts})")
        
        # All endpoints failed
        raise last_error or Exception("All endpoints failed")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((Exception,)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def call_with_retry(
        self,
        messages: List[Dict[str, str]],
        max_completion_tokens: int = 4096,
        temperature: float = 0.1,
        **kwargs
    ) -> str:
        """
        Make an API call with tenacity retry logic.
        
        This method wraps call_with_failover with additional retry logic
        for transient failures.
        
        Args:
            messages: Chat messages
            max_completion_tokens: Max tokens in response  
            temperature: Sampling temperature
            **kwargs: Additional arguments
            
        Returns:
            Model response text
        """
        return await self.call_with_failover(
            messages=messages,
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            **kwargs
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get router statistics."""
        return {
            "total_requests": self._total_requests,
            "successful_requests": self._successful_requests,
            "failed_requests": self._failed_requests,
            "failovers": self._failovers,
            "success_rate": (self._successful_requests / self._total_requests * 100) 
                           if self._total_requests > 0 else 0,
            "endpoints": [
                {
                    "name": e.name,
                    "status": e.status.value,
                    "requests_this_minute": e.requests_this_minute,
                    "consecutive_failures": e.consecutive_failures,
                    "weight": e.weight
                }
                for e in self.endpoints
            ]
        }
    
    def get_healthy_endpoint_count(self) -> int:
        """Get number of healthy endpoints."""
        return sum(1 for e in self.endpoints if e.status == EndpointStatus.HEALTHY)
    
    def get_total_capacity(self) -> Dict[str, int]:
        """Get total available capacity across all healthy endpoints."""
        healthy = [e for e in self.endpoints if e.status in 
                  (EndpointStatus.HEALTHY, EndpointStatus.DEGRADED)]
        return {
            "rpm": sum(e.rpm_limit for e in healthy),
            "tpm": sum(e.tpm_limit for e in healthy)
        }


# Singleton instance for easy access
_router_instance: Optional[AzureEndpointRouter] = None


def get_router() -> AzureEndpointRouter:
    """Get or create the singleton router instance."""
    global _router_instance
    if _router_instance is None:
        _router_instance = AzureEndpointRouter()
    return _router_instance


def reset_router():
    """Reset the singleton router (useful for testing)."""
    global _router_instance
    _router_instance = None
