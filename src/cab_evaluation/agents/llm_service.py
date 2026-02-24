"""LLM service for managing model interactions."""

import json
import os
import time
import logging
from typing import Optional, Dict, Any

import asyncio
import boto3
from botocore.config import Config
from openai import OpenAI, AzureOpenAI, AsyncAzureOpenAI
from dotenv import load_dotenv

# Azure Key Vault integration
try:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    AZURE_KEYVAULT_AVAILABLE = True
except ImportError:
    AZURE_KEYVAULT_AVAILABLE = False

# Azure Endpoint Router for multi-endpoint load balancing
from .azure_endpoint_router import get_router, AzureEndpointRouter

from ..core.config import CABConfig, ModelConfig
from ..core.exceptions import LLMError, InputTooLongError
from ..prompts.constants import ValidationPatterns

logger = logging.getLogger(__name__)


class LLMService:
    """Service for managing LLM model interactions."""
    
    def __init__(self, config: CABConfig, use_azure_router: bool = True):
        """Initialize LLM service.
        
        Args:
            config: CAB configuration
            use_azure_router: Whether to use the multi-endpoint Azure router (default: True)
        """
        self.config = config
        self.use_azure_router = use_azure_router
        
        # Setup AWS Bedrock client
        bedrock_config = Config(
            retries={"max_attempts": 1000, "mode": "standard"},
            connect_timeout=120,
            read_timeout=1200
        )
        self.bedrock_client = boto3.client(
            'bedrock-runtime', 
            config=bedrock_config, 
            region_name='us-west-2'
        )
        
        # Load environment variables for OpenAI
        load_dotenv()
        
        # Initialize OpenAI client cache
        self._openai_clients: Dict[str, OpenAI] = {}
        
        # Initialize Azure OpenAI client cache (async for parallel processing)
        self._azure_openai_clients: Dict[str, AsyncAzureOpenAI] = {}
        
        # Initialize Azure Endpoint Router for multi-endpoint load balancing
        self._azure_router: Optional[AzureEndpointRouter] = None
        if self.use_azure_router:
            try:
                self._azure_router = get_router()
                capacity = self._azure_router.get_total_capacity()
                logger.debug(f"Azure Router: {len(self._azure_router.endpoints)} endpoints, "
                            f"{capacity['rpm']:,} RPM")
            except Exception as e:
                logger.warning(f"Failed to initialize Azure Router, falling back to single endpoint: {e}")
                self._azure_router = None
    
    def _get_openai_client(self, api_key_env_var: str) -> OpenAI:
        """Get or create OpenAI client."""
        if api_key_env_var not in self._openai_clients:
            api_key = os.getenv(api_key_env_var)
            if not api_key:
                raise LLMError(f"Missing environment variable: {api_key_env_var}")
            self._openai_clients[api_key_env_var] = OpenAI(api_key=api_key)
        return self._openai_clients[api_key_env_var]
    
    def _get_api_key_from_keyvault(self, secret_name: str) -> str:
        """Retrieve API key from Azure Key Vault."""
        # Default Key Vault for CodeAssistBench
        KEYVAULT_URL = "https://abadawikeys.vault.azure.net"
        
        if not AZURE_KEYVAULT_AVAILABLE:
            raise LLMError(
                "Azure Key Vault SDK not installed. Run: pip install azure-identity azure-keyvault-secrets"
            )
        
        try:
            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=KEYVAULT_URL, credential=credential)
            secret = client.get_secret(secret_name)
            logger.debug(f"Retrieved API key from Key Vault: {secret_name}")
            return secret.value
        except Exception as e:
            raise LLMError(f"Failed to retrieve secret '{secret_name}' from Key Vault: {e}")
    
    def _get_azure_openai_client(self, model_config: ModelConfig) -> AsyncAzureOpenAI:
        """Get or create async Azure OpenAI client for parallel processing."""
        # Default Azure OpenAI endpoint (East US 2 - deepprompteastus2)
        DEFAULT_AZURE_ENDPOINT = "https://deepprompteastus2.openai.azure.com"
        # Default Key Vault secret name for GPT-5.2
        DEFAULT_KEYVAULT_SECRET = "gpt-5-2-api-key"
        
        cache_key = f"{model_config.azure_endpoint_env_var}_{model_config.api_key_env_var}"
        
        if cache_key not in self._azure_openai_clients:
            # Try environment variable first, then Key Vault
            api_key = os.getenv(model_config.api_key_env_var)
            
            if not api_key:
                logger.debug(f"Env var {model_config.api_key_env_var} not set, using Key Vault")
                api_key = self._get_api_key_from_keyvault(DEFAULT_KEYVAULT_SECRET)
            
            # Use environment variable or default endpoint
            azure_endpoint = os.getenv(model_config.azure_endpoint_env_var, DEFAULT_AZURE_ENDPOINT)
            
            # Ensure endpoint has https:// prefix
            if not azure_endpoint.startswith("https://"):
                azure_endpoint = f"https://{azure_endpoint}"
            
            api_version = os.getenv("AZURE_OPENAI_API_VERSION", model_config.azure_api_version)
            
            # Use AsyncAzureOpenAI for true async/parallel processing
            self._azure_openai_clients[cache_key] = AsyncAzureOpenAI(
                api_key=api_key,
                api_version=api_version,
                azure_endpoint=azure_endpoint
            )
            logger.debug(f"Created Azure client: {azure_endpoint}")
        
        return self._azure_openai_clients[cache_key]
    
    def _is_input_too_long_error(self, error_message: str) -> bool:
        """Check if error indicates input too long."""
        error_text = str(error_message).lower()
        for pattern in ValidationPatterns.INPUT_TOO_LONG_PATTERNS:
            if pattern.lower() in error_text:
                return True
        return False
    
    def _check_input_size(self, user_prompt: str, system_prompt: str, model_config: ModelConfig):
        """Check if input size exceeds model limits."""
        total_prompt_size = len(user_prompt) + len(system_prompt)
        if total_prompt_size > model_config.max_tokens:
            raise InputTooLongError(
                f"Prompt size too large ({total_prompt_size} chars) for model {model_config.name} "
                f"(limit: {model_config.max_tokens})"
            )
    
    async def call_model(
        self,
        user_prompt: str,
        system_prompt: str,
        model_config: ModelConfig,
        agent_type: str = "unknown",
        issue_id: str = "unknown",
        max_retries: int = 1000
    ) -> str:
        """Call LLM model with given prompts and retry logic.
        
        Args:
            user_prompt: User input prompt
            system_prompt: System prompt
            model_config: Model configuration
            agent_type: Type of agent making the call
            issue_id: Issue ID for tracking
            max_retries: Maximum number of retries
            
        Returns:
            Model response text
            
        Raises:
            InputTooLongError: If input exceeds model context window
            LLMError: If model call fails after retries
        """
        # Check input size
        self._check_input_size(user_prompt, system_prompt, model_config)
        
        retry_count = 0
        while retry_count <= max_retries:
            try:
                if model_config.provider == "openai":
                    return await self._call_openai_model(
                        user_prompt, system_prompt, model_config
                    )
                elif model_config.provider == "azure_openai":
                    return await self._call_azure_openai_model(
                        user_prompt, system_prompt, model_config
                    )
                else:  # bedrock
                    return await self._call_bedrock_model(
                        user_prompt, system_prompt, model_config
                    )
                    
            except Exception as e:
                error_str = str(e)
                error_lower = error_str.lower()
                
                # Check for input too long errors
                if self._is_input_too_long_error(error_str):
                    logger.warning(f"Input size error: {error_str}")
                    raise InputTooLongError(error_str)
                
                # Check for rate limit errors - use longer waits with exponential backoff
                is_rate_limit = any(term in error_lower for term in [
                    "429", "rate limit", "ratelimit", "throttl", "too many requests", "quota", "capacity"
                ])
                
                retry_count += 1
                if retry_count <= max_retries:
                    # Use exponential backoff for rate limits, shorter for other errors
                    import random
                    if is_rate_limit:
                        # Exponential backoff: 30s, 45s, 67s, 100s, 120s (capped)
                        base_wait = 30
                        wait_time = min(base_wait * (1.5 ** min(retry_count - 1, 4)) + random.uniform(0, 15), 120)
                        logger.warning(f"Rate limited (429). Retry {retry_count}/{max_retries} in {wait_time:.0f}s...")
                    else:
                        wait_time = 10 + random.uniform(0, 5)
                        logger.warning(
                            f"LLM call failed (attempt {retry_count}/{max_retries}). "
                            f"Retrying in {wait_time:.1f}s. Error: {error_str[:200]}"
                        )
                    await asyncio.sleep(wait_time)  # Non-blocking sleep for async
                else:
                    logger.error(f"LLM call failed after {max_retries} retries: {error_str}")
                    raise LLMError(
                        f"Failed to call {model_config.name} after {max_retries} retries: {error_str}",
                        model_name=model_config.name,
                        retry_count=retry_count
                    )
    
    async def _call_openai_model(
        self,
        user_prompt: str,
        system_prompt: str,
        model_config: ModelConfig
    ) -> str:
        """Call OpenAI model."""
        client = self._get_openai_client(model_config.api_key_env_var)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        # Reasoning models (e.g. o1*) reject `max_tokens` and also don't support `temperature`.
        model_id = (model_config.model_id or "").lower()
        is_o1 = model_id == "o1" or model_id.startswith("o1-")
        is_gpt5 = model_id == "gpt-5" or model_id.startswith("gpt-5-")
        is_reasoning_model = is_gpt5 or is_o1

        if is_reasoning_model:
            response = client.chat.completions.create(
                model=model_config.model_id,
                messages=messages,
                max_completion_tokens=model_config.max_tokens,
                reasoning_effort="low",
            )
        else:
            response = client.chat.completions.create(
                model=model_config.model_id,
                messages=messages,
                max_tokens=model_config.max_tokens,
                temperature=model_config.temperature,
            )

        return response.choices[0].message.content
    
    async def _call_azure_openai_model(
        self,
        user_prompt: str,
        system_prompt: str,
        model_config: ModelConfig
    ) -> str:
        """Call Azure OpenAI model with multi-endpoint routing and failover."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        # Check if this is a reasoning model that doesn't support temperature
        model_id = (model_config.model_id or "").lower()
        is_o1 = model_id == "o1" or model_id.startswith("o1-")
        is_gpt5 = model_id == "gpt-5" or model_id.startswith("gpt-5-")
        is_reasoning_model = is_gpt5 or is_o1
        
        # Use the multi-endpoint router if available (default)
        if self._azure_router is not None:
            if is_reasoning_model:
                # Reasoning models don't support temperature parameter
                return await self._azure_router.call_with_retry(
                    messages=messages,
                    max_completion_tokens=model_config.max_tokens,
                    reasoning_effort="low",
                )
            else:
                return await self._azure_router.call_with_retry(
                    messages=messages,
                    max_completion_tokens=model_config.max_tokens,
                    temperature=model_config.temperature,
                )
        
        # Fallback to single endpoint if router not available
        client = self._get_azure_openai_client(model_config)
        
        # Use deployment name if specified, otherwise use model_id
        deployment_name = model_config.azure_deployment_name or model_config.model_id
        
        # GPT-5.2 and newer models use max_completion_tokens instead of max_tokens
        # Using await for true async execution
        if is_reasoning_model:
            # Reasoning models don't support temperature, use reasoning_effort instead
            response = await client.chat.completions.create(
                model=deployment_name,
                messages=messages,
                max_completion_tokens=model_config.max_tokens,
                reasoning_effort="low",
            )
        else:
            response = await client.chat.completions.create(
                model=deployment_name,
                messages=messages,
                max_completion_tokens=model_config.max_tokens,
                temperature=model_config.temperature,
            )
        
        return response.choices[0].message.content
    
    async def _call_bedrock_model(
        self,
        user_prompt: str,
        system_prompt: str,
        model_config: ModelConfig
    ) -> str:
        """Call Bedrock model."""
        # Determine model type for payload formatting
        is_claude = "anthropic" in model_config.model_id.lower()
        is_llama = "llama" in model_config.model_id.lower() 
        is_deepseek = "deepseek" in model_config.model_id.lower()
        
        if is_claude:
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
                "max_tokens": model_config.max_tokens,
                "temperature": model_config.temperature,
            }
            
            # Add thinking mode if enabled
            if model_config.thinking_enabled:
                body["thinking"] = {"type": "enabled", "budget_tokens": 30000}
                
        elif is_llama:
            # Use standard Meta chat format
            formatted_prompt = f"<|system|>\n{system_prompt}<|end|>\n<|user|>\n{user_prompt}<|end|>\n<|assistant|>"
            body = {
                "prompt": formatted_prompt,
                "max_gen_len": model_config.max_tokens,
                "temperature": model_config.temperature,
            }
        else:  # DeepSeek and others
            body = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": model_config.max_tokens,
                "temperature": model_config.temperature,
            }
        
        response = self.bedrock_client.invoke_model(
            body=json.dumps(body),
            modelId=model_config.model_id,
            accept="application/json",
            contentType="application/json",
        )
        
        response_body = json.loads(response.get("body").read())
        
        # Extract response based on model type
        if is_claude:
            if model_config.thinking_enabled:
                return response_body["content"][1]["text"]
            else:
                return response_body["content"][0]["text"]
        elif is_llama:
            raw_response = response_body.get("generation", response_body.get("completion", ""))
            # Clean up the response by removing end tokens
            if "<|end|>" in raw_response:
                return raw_response.split("<|end|>")[0].strip()
            return raw_response.strip()
        else:  # DeepSeek format
            return response_body["choices"][0]["message"]["content"]
    
    def get_azure_router_stats(self) -> Optional[Dict[str, Any]]:
        """Get statistics from the Azure endpoint router."""
        if self._azure_router:
            return self._azure_router.get_stats()
        return None
