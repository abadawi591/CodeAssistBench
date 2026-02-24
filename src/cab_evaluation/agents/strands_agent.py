"""Strands agent implementation for CAB evaluation."""

import os
import json
import time
import asyncio
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from pathlib import Path

from .base_agent import BaseAgent
from ..core.config import CABConfig, ModelConfig
from ..core.exceptions import AgentError, LLMError
from ..prompts.prompt_manager import PromptManager

import logging

logger = logging.getLogger(__name__)

# Global flag to only warn once about Strands not being available
_strands_warning_shown = False

# Global cache for Azure OpenAI configuration (avoid repeated Key Vault lookups)
_azure_config_cache = {}

# Global rate limit state - shared across all concurrent issues
import threading
_rate_limit_lock = threading.Lock()
_endpoint_rate_limits = {}  # endpoint_name -> (rate_limited_until_timestamp, consecutive_429s)
_endpoint_rotation_counter = 0  # For round-robin distribution

# Custom JSON encoder to handle non-serializable objects
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)

# Pricing for Claude Sonnet 4 (per 1M tokens) - Updated for Nov 2024
PRICING = {
    "input_base": 3.00,           # Base input price per 1M tokens
    "output": 15.00,              # Output price per 1M tokens
    "cache_write": 3.75,          # Cache write price per 1M tokens (1.25x base)
    "cache_read": 0.30,           # Cache read price per 1M tokens (0.1x base)
}


class StrandsAgent(BaseAgent):
    """Agent that uses the Strands framework for enhanced tool capabilities."""
    
    def __init__(
        self,
        model_name: str = "sonnet37",
        config: Optional[CABConfig] = None,
        prompt_manager: Optional[PromptManager] = None,
        read_only: bool = False,
        **kwargs
    ):
        """Initialize Strands agent.
        
        Args:
            model_name: Model to use (defaults to sonnet37)
            config: CAB configuration
            prompt_manager: Prompt manager
            read_only: If True, only read-only tools are enabled
            **kwargs: Additional arguments for base agent
        """
        # Set read_only first so it's available during initialization
        self.read_only = read_only
        
        super().__init__(
            agent_type="strands",
            model_name=model_name,
            config=config,
            prompt_manager=prompt_manager
        )
        
        self._strands_agent = None
        self._strands_tools = None
        self._strands_available = True  # Will be set to False if Strands import fails
        self._tool_agent = None  # Fallback tool agent when Strands unavailable
        self._current_repo_dir = None  # Repository directory for tool agent
        self._setup_strands_environment()
        
    def _setup_strands_environment(self):
        """Setup Strands environment and tools."""
        # Set environment variable for execute_bash based on read-only mode
        if not self.read_only:
            # Enable unrestricted bash execution in write-allowed mode
            os.environ["EXECUTE_BASH_UNRESTRICTED"] = "true"
        else:
            # Ensure restricted mode in read-only mode
            os.environ["EXECUTE_BASH_UNRESTRICTED"] = "false"
    
    def set_repo_dir(self, repo_dir: str):
        """Set the repository directory for tool-based exploration."""
        self._current_repo_dir = repo_dir
        self._tool_agent = None  # Reset tool agent for new repo
    
    async def _run_tool_agent(
        self, 
        user_prompt: str, 
        system_prompt: str, 
        repo_dir: str,
        issue_id: str = "unknown"
    ) -> str:
        """
        Run the tool agent to explore code and answer questions.
        Replicates Strands functionality using Azure OpenAI function calling.
        """
        from .tool_agent import ToolAgent
        from ..agents.azure_endpoint_router import get_router
        
        start_time = time.time()
        
        try:
            # Get Azure client from router
            router = get_router()
            endpoint = router._select_endpoint()
            client = router._get_client(endpoint)
            
            # Create tool agent
            tool_agent = ToolAgent(
                client=client,
                deployment_name=endpoint.deployment_name,
                repo_dir=repo_dir,
                max_iterations=10,
                read_only=self.read_only
            )
            
            # Enhance system prompt with tool instructions
            enhanced_system = system_prompt + """

You have access to tools to explore the codebase:
- fs_read: Read file contents
- execute_bash: Run commands (grep, find, ls, etc.)
- list_directory: List directory contents
- thinking: Record your reasoning
- provide_answer: Give your final answer

Explore the code thoroughly before answering. Use the tools to:
1. Understand the project structure (list_directory)
2. Find relevant files (execute_bash with grep/find)
3. Read the code (fs_read)
4. Analyze and provide a helpful answer (provide_answer)
"""
            
            # Run the agent
            answer, metadata = await tool_agent.run(enhanced_system, user_prompt)
            
            elapsed = time.time() - start_time
            self.logger.debug(
                f"Tool agent completed: {metadata['iterations']} iterations, "
                f"{metadata['tool_calls']} tool calls, {elapsed:.1f}s"
            )
            
            return answer
            
        except Exception as e:
            self.logger.error(f"Tool agent failed: {e}")
            # Fallback to direct LLM call via parent
            return await super().call_llm(user_prompt, system_prompt, issue_id)
    
    async def call_llm(
        self,
        user_prompt: str,
        system_prompt: str,
        issue_id: str = "unknown",
        **kwargs
    ) -> str:
        """
        Override call_llm to use tool agent when repo_dir is available.
        This replicates Strands functionality for code exploration.
        """
        repo_dir = kwargs.get('repo_dir', self._current_repo_dir)
        
        # Use tool agent if we have a repo directory and Strands is not available
        if repo_dir and not getattr(self, '_strands_available', True):
            return await self._run_tool_agent(user_prompt, system_prompt, repo_dir, issue_id)
        
        # Otherwise use parent's call_llm (direct LLM service)
        return await super().call_llm(user_prompt, system_prompt, issue_id, **kwargs)
    
    def _is_azure_model(self, model_name: str) -> bool:
        """Check if the model is an Azure OpenAI model.
        
        Args:
            model_name: Model name
            
        Returns:
            True if Azure OpenAI model
        """
        azure_models = {"gpt-5.2", "gpt52", "gpt-5", "gpt5"}
        return model_name.lower().replace(".", "-").replace("_", "-") in azure_models or model_name.startswith("gpt-5")
    
    def _get_azure_openai_config(self, model_name: str, force_rotation: bool = False) -> dict:
        """Get Azure OpenAI configuration for Strands OpenAIModel.
        
        Uses endpoint rotation to spread load across multiple Azure endpoints.
        Respects rate limit state to avoid endpoints that are still cooling down.
        
        Args:
            model_name: Model name
            force_rotation: If True, always select next endpoint (after rate limit)
            
        Returns:
            Dict with api_key, base_url, deployment_name
        """
        global _azure_config_cache, _endpoint_rotation_counter, _endpoint_rate_limits
        
        # Define all available endpoints with capacity (same as azure_endpoint_router.py)
        ENDPOINTS = [
            {
                "name": "eastus2-primary",
                "endpoint": "https://deepprompteastus2.openai.azure.com",
                "deployment_name": "gpt-5.2",
                "keyvault_secret": "gpt-5-2-api-key",
                "rpm_limit": 10000,
                "tpm_limit": 1000000,
                "priority": 1,
            },
            {
                "name": "southcentralus",
                "endpoint": "https://deeppromptsouthcentralus.openai.azure.com",
                "deployment_name": "gpt-5.2_2",
                "keyvault_secret": "gpt-5-2-number-2-api-key",
                "rpm_limit": 10000,
                "tpm_limit": 1000000,
                "priority": 2,
            },
            {
                "name": "swedencentral",
                "endpoint": "https://deeppromptswedencentral.openai.azure.com",
                "deployment_name": "gpt-5.2",
                "keyvault_secret": "gpt-5-2-number-3-api-key",
                "rpm_limit": 8500,
                "tpm_limit": 850000,
                "priority": 3,
            },
            {
                "name": "eastus2-secondary",
                "endpoint": "https://deepprompteastus2.openai.azure.com",
                "deployment_name": "gpt-5.2-4",
                "keyvault_secret": "gpt-5-2-number-4-api-key",
                "rpm_limit": 2000,
                "tpm_limit": 200000,
                "priority": 4,
            },
        ]
        
        # Calculate weights for each endpoint (based on capacity and rate limit status)
        def get_endpoint_weight(ep: dict, current_time: float) -> float:
            """Calculate routing weight based on capacity, priority, and rate limit status."""
            ep_name = ep["name"]
            
            # If rate-limited, weight is 0
            if ep_name in _endpoint_rate_limits:
                rate_limited_until, _ = _endpoint_rate_limits[ep_name]
                if current_time < rate_limited_until:
                    return 0.0
                else:
                    # Cooldown expired, clear the rate limit
                    del _endpoint_rate_limits[ep_name]
            
            # Base weight from RPM capacity (normalized)
            base_weight = ep["rpm_limit"] / 1000  # 10000 RPM = weight 10, 2000 RPM = weight 2
            
            # Adjust by priority (priority 1 = 1.0x, priority 4 = 0.7x)
            priority_factor = 1.0 - (ep["priority"] - 1) * 0.1
            
            return base_weight * priority_factor
        
        # Select endpoint using weighted random selection
        current_time = time.time()
        selected_endpoint = None
        
        with _rate_limit_lock:
            import random
            
            # Calculate weights for all endpoints
            weights = [(ep, get_endpoint_weight(ep, current_time)) for ep in ENDPOINTS]
            available = [(ep, w) for ep, w in weights if w > 0]
            
            if available:
                # Weighted random selection
                total_weight = sum(w for _, w in available)
                r = random.random() * total_weight
                
                cumulative = 0
                for ep, weight in available:
                    cumulative += weight
                    if r <= cumulative:
                        selected_endpoint = ep
                        break
                
                # Fallback to first available
                if selected_endpoint is None:
                    selected_endpoint = available[0][0]
                    
                self.logger.debug(f"Weighted selection: {[(ep['name'], f'{w:.1f}') for ep, w in available]}")
            else:
                # All endpoints rate-limited - select one with shortest cooldown
                self.logger.warning("All endpoints rate-limited! Selecting one with shortest cooldown.")
                min_wait = float('inf')
                for ep in ENDPOINTS:
                    if ep["name"] in _endpoint_rate_limits:
                        until, _ = _endpoint_rate_limits[ep["name"]]
                        wait = until - current_time
                        if wait < min_wait:
                            min_wait = wait
                            selected_endpoint = ep
                if selected_endpoint is None:
                    selected_endpoint = ENDPOINTS[0]
        
        ep_name = selected_endpoint["name"]
        secret_name = selected_endpoint["keyvault_secret"]
        
        # Check API key cache (keyed by secret name, not endpoint)
        if secret_name not in _azure_config_cache:
            # Get API key from Key Vault
            api_key = None
            try:
                from azure.identity import DefaultAzureCredential
                from azure.keyvault.secrets import SecretClient
                
                key_vault_name = os.getenv("AZURE_KEY_VAULT_NAME", "abadawikeys")
                key_vault_url = f"https://{key_vault_name}.vault.azure.net"
                credential = DefaultAzureCredential()
                client = SecretClient(vault_url=key_vault_url, credential=credential)
                api_key = client.get_secret(secret_name).value
                _azure_config_cache[secret_name] = api_key
                self.logger.debug(f"Retrieved API key for {ep_name} from Key Vault")
            except Exception as e:
                self.logger.warning(f"Failed to get API key from Key Vault for {ep_name}: {e}")
                # Try environment variable as fallback
                api_key = os.getenv("AZURE_OPENAI_API_KEY")
        else:
            api_key = _azure_config_cache[secret_name]
        
        # API version
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
        
        config = {
            "api_key": api_key,
            "endpoint": selected_endpoint["endpoint"],
            "deployment_name": selected_endpoint["deployment_name"],
            "api_version": api_version,
            "endpoint_name": ep_name  # Track which endpoint we're using
        }
        
        self.logger.debug(f"Selected endpoint: {ep_name} ({selected_endpoint['deployment_name']})")
        
        return config
    
    def _mark_endpoint_rate_limited(self, endpoint_name: str, cooldown_seconds: int = 65):
        """Mark an endpoint as rate-limited.
        
        Called when we receive a 429 error. Updates global state so other
        concurrent issues know to avoid this endpoint.
        
        Args:
            endpoint_name: Name of the rate-limited endpoint
            cooldown_seconds: How long to wait before retrying (default 65s for Azure's 60s + buffer)
        """
        global _endpoint_rate_limits
        
        with _rate_limit_lock:
            current_time = time.time()
            
            # Track consecutive 429s for this endpoint
            if endpoint_name in _endpoint_rate_limits:
                _, consecutive = _endpoint_rate_limits[endpoint_name]
                consecutive += 1
                # Increase cooldown exponentially for repeated 429s
                cooldown_seconds = min(cooldown_seconds * (1.5 ** min(consecutive, 4)), 300)
            else:
                consecutive = 1
            
            rate_limited_until = current_time + cooldown_seconds
            _endpoint_rate_limits[endpoint_name] = (rate_limited_until, consecutive)
            
            self.logger.warning(f"Marked {endpoint_name} rate-limited for {cooldown_seconds:.0f}s (consecutive: {consecutive})")
    
    def _get_strands_model_id(self, model_name: str) -> str:
        """Map CAB model names to Strands BedrockModel IDs.
        
        Args:
            model_name: CAB model name
            
        Returns:
            Strands model ID
        """
        # Model mapping from CAB to Strands/Bedrock
        model_mapping = {
            "haiku": "us.anthropic.claude-3-5-haiku-20241022-v1:0",
            "sonnet": "us.anthropic.claude-3-7-sonnet-20250219-v1:0", 
            "sonnet37": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
            "thinking": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
            "deepseek": "us.deepseek.r1-v1:0",
            "llama": "us.meta.llama3-3-70b-instruct-v1:0",
            # Default fallback to sonnet37 equivalent
            "default": "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
        }
        
        return model_mapping.get(model_name, model_mapping["default"])
    
    def _build_strands_agent(self, system_prompt: str):
        """Build and configure a Strands Agent instance.
        
        Args:
            system_prompt: System prompt for the agent
            
        Returns:
            Configured Strands Agent or None if Strands is not available
        """
        # Use class-level flag to only log initialization once
        if not hasattr(self.__class__, '_strands_init_logged'):
            self.__class__._strands_init_logged = False
        
        log_init = not self.__class__._strands_init_logged
        if log_init:
            self.logger.info(f"Initializing Strands agent for model: {self.model_name}")
        else:
            self.logger.debug(f"Building Strands agent for model: {self.model_name}")
        
        try:
            # Import Strands components
            from strands import Agent
            from strands.models import BedrockModel
            from strands.hooks import HookProvider, MessageAddedEvent
            
            # Import Strands tools
            from tools.src.strands_tools import (
                execute_bash, fs_read, fs_write, report_issue, use_aws, thinking
            )
            self.logger.debug("Strands framework imported")
        except ImportError as e:
            global _strands_warning_shown
            if not _strands_warning_shown:
                self.logger.warning(f"Strands import failed: {e} - falling back to Tool Agent")
                _strands_warning_shown = True
            self._strands_available = False
            return None
        
        # Check if this is an Azure OpenAI model
        if self._is_azure_model(self.model_name):
            self.logger.debug(f"Configuring Strands with Azure OpenAI")
            try:
                from strands.models.openai import OpenAIModel
                self.logger.debug("OpenAIModel imported")
                
                # Get config with endpoint rotation (force_rotation=True if rebuilding after rate limit)
                force_rotation = self._strands_agent is not None  # Rebuilding = need different endpoint
                azure_config = self._get_azure_openai_config(self.model_name, force_rotation=force_rotation)
                
                # Track current endpoint for rate limit marking
                self._current_endpoint_name = azure_config.get("endpoint_name", "unknown")
                
                self.logger.debug(f"Azure config: endpoint={self._current_endpoint_name}, deployment={azure_config['deployment_name']}")
                
                if not azure_config["api_key"]:
                    self.logger.error("Azure OpenAI API key not found")
                    self._strands_available = False
                    return None
                
                # Build Azure OpenAI base URL
                # Format: https://{resource}.openai.azure.com/openai/deployments/{deployment}/
                base_url = f"{azure_config['endpoint']}/openai/deployments/{azure_config['deployment_name']}/"
                
                # Create OpenAI model configured for Azure
                model = OpenAIModel(
                    client_args={
                        "api_key": azure_config["api_key"],
                        "base_url": base_url,
                        "default_headers": {
                            "api-key": azure_config["api_key"]
                        },
                        "default_query": {
                            "api-version": azure_config["api_version"]
                        }
                    },
                    model_id=azure_config["deployment_name"],
                    params={
                        "max_completion_tokens": 4096,
                        # Note: temperature is not supported by GPT-5.2 reasoning models (only default=1)
                        "reasoning_effort": "low",  # Reduce reasoning for faster responses
                    }
                )
                
                if log_init:
                    self.logger.info(f"Strands Azure OpenAI ready: {self._current_endpoint_name} ({azure_config['deployment_name']})")
                    self.__class__._strands_init_logged = True
                else:
                    self.logger.debug(f"Created Strands agent on {self._current_endpoint_name}")
                
            except ImportError as e:
                self.logger.warning(f"Failed to import OpenAIModel: {e}")
                self._strands_available = False
                return None
            except Exception as e:
                self.logger.error(f"Failed to create Azure OpenAI model: {e}")
                self._strands_available = False
                return None
        else:
            # Use Bedrock model for non-Azure models
            model_id = self._get_strands_model_id(self.model_name)
            
            # Create Bedrock model with caching enabled and temperature=0 for determinism
            model = BedrockModel(
                model_id=model_id,
                region_name="us-west-2",
                max_retries=1000,
                cache_prompt="default",  # Cache system prompt
                cache_tools="default",   # Cache tools
                temperature=0.0          # Set to 0 for deterministic outputs
            )
            
            self.logger.debug(f"Created Strands BedrockModel: {model_id}")
        
        # Select tools based on read-only mode
        if self.read_only:
            # Read-only mode: only allow safe read operations
            tools = [execute_bash, fs_read, thinking]
            self.logger.debug("Strands READ-ONLY mode")
        else:
            # Default mode: all tools enabled including write operations
            tools = [execute_bash, fs_read, fs_write, report_issue, use_aws, thinking]
            self.logger.debug("Strands WRITE mode")
        
        # Create cache point hook to enable prompt caching
        cache_hook = CachePointHook()
        
        # Create Strands agent with the configured model and tools
        # Set callback_handler=None to suppress streaming output to console
        strands_agent = Agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            hooks=[cache_hook],
            callback_handler=None  # Suppress console streaming
        )
        
        self.logger.debug(f"Strands agent created")
        return strands_agent
    
    def get_system_prompt(self, **kwargs) -> str:
        """Get system prompt for this agent type.
        
        Args:
            **kwargs: Additional context for prompt generation
            
        Returns:
            System prompt string
        """
        # Use a generic system prompt since Strands agent can be flexible
        base_prompt = """You are an AI assistant with comprehensive tool capabilities including:
- File system operations (read/write files and directories)
- Bash command execution with safety controls
- Issue reporting and AWS service interaction
- Advanced reasoning with the thinking tool

You have access to the filesystem and can execute commands to help solve programming problems.
Answer questions accurately and provide practical solutions with code when appropriate.
Be concise but thorough in your explanations."""
        
        # Add specific context if provided
        if kwargs.get('context'):
            base_prompt += f"\n\nAdditional context: {kwargs['context']}"
            
        return base_prompt
    
    async def generate_response(
        self,
        user_prompt: str,
        system_prompt: str,
        issue_id: str = "unknown",
        **kwargs
    ) -> str:
        """Generate response using Strands Agent or fallback to direct LLM.
        
        Args:
            user_prompt: User input prompt
            system_prompt: System prompt to use
            issue_id: Issue ID for tracking
            **kwargs: Additional arguments
            
        Returns:
            Generated response text
            
        Raises:
            AgentError: If agent execution fails
        """
        # Increment counter
        self.increment_call_counter(issue_id)
        
        # Build Strands agent if not already created
        if self._strands_agent is None and getattr(self, '_strands_available', True):
            self._strands_agent = self._build_strands_agent(system_prompt)
        
        # Fallback to Tool Agent if Strands is not available
        if self._strands_agent is None or not getattr(self, '_strands_available', True):
            repo_dir = kwargs.get('repo_dir', self._current_repo_dir)
            if repo_dir:
                self.logger.debug("Using Tool Agent with function calling")
                return await self._run_tool_agent(user_prompt, system_prompt, repo_dir, issue_id)
            else:
                self.logger.debug("No repo_dir, using direct LLM")
                return await self.call_llm(user_prompt, system_prompt, issue_id, **kwargs)
        
        # Log prompts at DEBUG level
        self.logger.debug(f"System prompt: {len(system_prompt)} chars")
        self.logger.debug(f"User prompt: {len(user_prompt)} chars")
        
        start_time = time.time()
        self.logger.debug(f"Strands LLM call: {len(user_prompt):,} chars")
        
        # Get repo directory for tool usage
        repo_dir = kwargs.get('repo_dir', self._current_repo_dir)
        
        # Add repo path and tool instructions so the model knows how to explore
        tool_instructions = ""
        if repo_dir:
            tool_instructions = f"""

<repository_context>
The repository has been cloned to: {repo_dir}
When using execute_bash, set cwd="{repo_dir}"
When using fs_read, use paths relative to {repo_dir}

IMPORTANT: Use the tools to explore the codebase before answering!
- Use execute_bash with command="ls -la" and cwd="{repo_dir}" to see the repository structure
- Use execute_bash with command="grep -r 'keyword' ." and cwd="{repo_dir}" to search for code
- Use fs_read to read specific files
</repository_context>
"""
        
        # Add minimal instruction to encourage concise responses
        enhanced_prompt = user_prompt + tool_instructions + "\n\n<implicitInstruction>\n- Write only the ABSOLUTE MINIMAL amount of code needed to address the requirement correctly. Avoid verbose implementations and any code that doesn't directly contribute to the solution\n- Use the available tools (execute_bash, fs_read) to explore the repository before answering\n</implicitInstruction>"
        
        try:
            # Execute with retry logic for rate limiting
            # Run in thread pool to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            response = None
            max_retries = 50  # Reduced from 300 - with 4 endpoints, we shouldn't need this many
            base_wait = 15  # Base wait time in seconds
            current_endpoint_name = getattr(self, '_current_endpoint_name', 'unknown')
            
            for attempt in range(max_retries):
                try:
                    # Run synchronous Strands call in thread pool for true concurrency
                    response = await loop.run_in_executor(
                        None,  # Use default thread pool
                        self._strands_agent,
                        enhanced_prompt
                    )
                    break
                except Exception as e:
                    error_str = str(e).lower()
                    
                    # Check for corrupted tool call history (Strands bug - unrecoverable)
                    # This happens when a tool call is made but no response is recorded
                    is_tool_call_error = "tool_call" in error_str and "did not have response" in error_str
                    if is_tool_call_error:
                        self.logger.error(f"Strands tool call history corrupted (unrecoverable). Ending conversation.")
                        raise AgentError(f"STRANDS_CORRUPTED: Tool call history corrupted - {e}")
                    
                    # Check for any rate limit related error
                    is_rate_limit = any(term in error_str for term in [
                        "throttl", "429", "rate limit", "ratelimit", 
                        "too many requests", "quota", "capacity"
                    ])
                    
                    if is_rate_limit and attempt < max_retries - 1:
                        # Mark current endpoint as rate-limited and switch to another
                        self._mark_endpoint_rate_limited(current_endpoint_name)
                        
                        # Add jitter to prevent thundering herd (random 0-30s on top of base)
                        import random
                        jitter = random.uniform(0, 30)
                        
                        # Shorter wait since we're switching endpoints
                        wait_time = base_wait + jitter
                        
                        self.logger.warning(f"Rate limited on {current_endpoint_name}. Switching endpoint. Retry {attempt + 1}/{max_retries} in {wait_time:.0f}s...")
                        await asyncio.sleep(wait_time)  # Non-blocking sleep
                        
                        # Rebuild Strands agent with a different endpoint
                        self._strands_agent = None  # Force rebuild
                        self._strands_agent = self._build_strands_agent(system_prompt)
                        if self._strands_agent:
                            current_endpoint_name = getattr(self, '_current_endpoint_name', 'unknown')
                            self.logger.info(f"Switched to endpoint: {current_endpoint_name}")
                    else:
                        raise AgentError(f"Strands agent execution failed after {attempt + 1} attempts: {e}")
            
            elapsed_time = time.time() - start_time
            self.logger.debug(f"Strands response: {elapsed_time:.1f}s, {len(str(response)):,} chars")
            
            # Log metrics summary
            self._log_metrics_summary()
            
            self.logger.debug(f"Response preview: {str(response)[:300]}...")
            
            return str(response)
            
        except Exception as e:
            elapsed_time = time.time() - start_time
            self.logger.error(f"Strands agent call failed after {elapsed_time:.2f}s: {e}")
            raise AgentError(f"Strands agent execution failed: {e}")
    
    def _log_metrics_summary(self):
        """Log compact metrics from Strands agent."""
        if self._strands_agent is None:
            return
        
        try:
            metrics_summary = self._strands_agent.event_loop_metrics.get_summary()
            usage = metrics_summary["accumulated_usage"]
            
            # Calculate pricing
            pricing_info = self._calculate_cost(usage)
            
            # Compact one-line summary at DEBUG level
            input_tokens = usage.get('inputTokens', 0)
            output_tokens = usage.get('outputTokens', 0)
            total_cost = pricing_info['total_cost']
            cycles = metrics_summary['total_cycles']
            
            self.logger.debug(f"Strands: {input_tokens:,}in/{output_tokens:,}out tokens, ${total_cost:.4f}, {cycles} cycle(s)")
            
        except Exception as e:
            self.logger.debug(f"Error logging Strands metrics: {e}")
    
    def _calculate_cost(self, usage: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate the cost based on token usage.
        
        Args:
            usage: Dictionary containing token counts
            
        Returns:
            Dictionary with detailed cost breakdown
        """
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)
        cache_read_tokens = usage.get("cacheReadInputTokens", 0)
        cache_write_tokens = usage.get("cacheWriteInputTokens", 0)
        
        # Calculate costs (convert from per 1M to actual)
        input_cost = (input_tokens / 1_000_000) * PRICING["input_base"]
        output_cost = (output_tokens / 1_000_000) * PRICING["output"]
        cache_write_cost = (cache_write_tokens / 1_000_000) * PRICING["cache_write"]
        cache_read_cost = (cache_read_tokens / 1_000_000) * PRICING["cache_read"]
        
        total_cost = input_cost + output_cost + cache_write_cost + cache_read_cost
        
        return {
            "input_cost": round(input_cost, 6),
            "output_cost": round(output_cost, 6),
            "cache_write_cost": round(cache_write_cost, 6),
            "cache_read_cost": round(cache_read_cost, 6),
            "total_cost": round(total_cost, 6),
        }
    
    def _calculate_cache_efficiency(self, usage: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate cache hit rate and efficiency metrics.
        
        Args:
            usage: Dictionary containing token counts
            
        Returns:
            Dictionary with cache efficiency metrics
        """
        cache_read_tokens = usage.get("cacheReadInputTokens", 0)
        cache_write_tokens = usage.get("cacheWriteInputTokens", 0)
        input_tokens = usage.get("inputTokens", 0)
        
        total_input_with_cache = input_tokens + cache_read_tokens + cache_write_tokens
        
        cache_hit_rate = 0.0
        if total_input_with_cache > 0:
            cache_hit_rate = (cache_read_tokens / total_input_with_cache) * 100
        
        # Calculate savings from cache
        # Cache read is 10% cost of normal input, so 90% savings
        normal_cost_for_cached = (cache_read_tokens / 1_000_000) * PRICING["input_base"]
        actual_cache_cost = (cache_read_tokens / 1_000_000) * PRICING["cache_read"]
        cache_savings = normal_cost_for_cached - actual_cache_cost
        
        return {
            "cache_hit_rate_percent": round(cache_hit_rate, 2),
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cache_savings_usd": round(cache_savings, 6),
            "total_input_tokens_with_cache": total_input_with_cache,
        }
    
    def save_interaction_log(
        self,
        log_dir: str,
        issue_id: str,
        system_prompt: str,
        query: str,
        response: str,
        start_time: float,
        end_time: float,
    ) -> str:
        """Save comprehensive interaction log with metrics.
        
        Args:
            log_dir: Directory to save logs
            issue_id: Issue ID
            system_prompt: System prompt content
            query: User query
            response: Agent response
            start_time: Start timestamp
            end_time: End timestamp
            
        Returns:
            Path to saved log file
        """
        if self._strands_agent is None:
            self.logger.warning("No Strands agent available for logging")
            return ""
        
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        logfile = os.path.join(log_dir, f"strands_interaction_{ts}_{issue_id}.json")
        
        # Get metrics from agent
        metrics_summary = self._strands_agent.event_loop_metrics.get_summary()
        usage = metrics_summary["accumulated_usage"]
        
        # Calculate pricing and cache efficiency
        pricing_info = self._calculate_cost(usage)
        cache_efficiency = self._calculate_cache_efficiency(usage)
        
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "issue_id": issue_id,
            "model_name": self.model_name,
            "read_only_mode": self.read_only,
            "system_prompt": system_prompt,
            "query": query,
            "response": str(response),
            "conversation_history": getattr(self._strands_agent, "messages", []),
            
            # Token usage metrics
            "token_metrics": {
                "accumulated_usage": usage,
                "inputTokens": usage.get("inputTokens", 0),
                "outputTokens": usage.get("outputTokens", 0),
                "totalTokens": usage.get("totalTokens", 0),
                "cacheReadInputTokens": usage.get("cacheReadInputTokens", 0),
                "cacheWriteInputTokens": usage.get("cacheWriteInputTokens", 0),
            },
            
            # Pricing information
            "pricing": pricing_info,
            
            # Cache performance
            "cache_performance": cache_efficiency,
            
            # Performance metrics
            "performance_metrics": {
                "total_execution_time_secs": round(end_time - start_time, 3),
                "total_cycles": metrics_summary["total_cycles"],
                "total_cycle_duration_secs": round(metrics_summary["total_duration"], 3),
                "average_cycle_time_secs": round(metrics_summary["average_cycle_time"], 3),
                "bedrock_latency_ms": metrics_summary["accumulated_metrics"]["latencyMs"],
            },
            
            # Tool usage metrics
            "tool_usage": metrics_summary["tool_usage"],
            
            # Execution traces
            "traces": metrics_summary["traces"],
        }
        
        # Write full log with safe encoding
        with open(logfile, "w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2, cls=CustomJSONEncoder)
        
        self.logger.info(f"Strands interaction log saved to: {logfile}")
        return logfile


# Cache Point Hook implementation (from QCLI.py)
try:
    from strands.hooks import HookProvider, MessageAddedEvent
    
    class CachePointHook(HookProvider):
        """Hook that adds cache points to enable prompt caching.
        
        This hook runs after each message is added and ensures only the last user
        message with tool results has a cache point (following QDevScience pattern).
        """
        
        def message_added(self, event: MessageAddedEvent) -> None:
            """Called after a message is added to the agent's message history.
            
            Args:
                event: Event containing the agent instance and newly added message
            """
            agent = event.agent
            message = event.message
            
            logger.debug(f"[HOOK] message_added called: role={message.get('role')}, content_blocks={len(message.get('content', []))}")
            
            # Only process user messages with tool results (like QDevScience)
            if message.get("role") != "user":
                logger.debug(f"[HOOK] Skipping non-user message")
                return
            
            # Check if this message contains tool results
            has_tool_results = any(
                "toolResult" in block 
                for block in message.get("content", [])
            )
            
            logger.debug(f"[HOOK] User message has_tool_results={has_tool_results}")
            
            if not has_tool_results:
                logger.debug(f"[HOOK] Skipping user message without tool results")
                return  # Skip regular user messages
            
            # Remove cache points from ALL messages (including this one)
            removed_count = 0
            for msg in agent.messages:
                if "content" in msg:
                    before = len(msg["content"])
                    msg["content"] = [
                        block for block in msg["content"]
                        if "cachePoint" not in block
                    ]
                    removed_count += before - len(msg["content"])
            
            logger.debug(f"[HOOK] Removed {removed_count} cache points from all messages")
            
            # Find the last user message with tool results and add cache point
            last_tool_result_msg = None
            for msg in reversed(agent.messages):
                if msg.get("role") == "user":
                    if any("toolResult" in block for block in msg.get("content", [])):
                        last_tool_result_msg = msg
                        break
            
            if last_tool_result_msg and "content" in last_tool_result_msg:
                last_tool_result_msg["content"].append({"cachePoint": {"type": "default"}})
                logger.debug(f"✓ Cache point added to last tool result message (total messages: {len(agent.messages)})")
            else:
                logger.debug(f"[HOOK] ERROR: Could not find last tool result message")

except ImportError:
    # Fallback if Strands hooks are not available
    class CachePointHook:
        def message_added(self, event):
            pass
