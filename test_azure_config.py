#!/usr/bin/env python3
"""Test script to verify Azure OpenAI configuration for CodeAssistBench.

This script auto-retrieves the API key from Azure Key Vault (abadawikeys).
Just make sure you're logged into Azure:
    az login

Or set the API key manually:
    export AZURE_OPENAI_API_KEY="<your-api-key>"

The endpoint defaults to: https://deepprompteastus2.openai.azure.com

Run with: python test_azure_config.py
"""

import os
import sys
import logging

# Azure Key Vault configuration
KEYVAULT_URL = "https://abadawikeys.vault.azure.net"
KEYVAULT_SECRET_NAME = "gpt-5-2-api-key"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


def get_api_key_from_keyvault():
    """Retrieve API key from Azure Key Vault."""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=KEYVAULT_URL, credential=credential)
        secret = client.get_secret(KEYVAULT_SECRET_NAME)
        return secret.value
    except ImportError:
        print("  ⚠️  Azure SDK not installed. Run: pip install azure-identity azure-keyvault-secrets")
        return None
    except Exception as e:
        print(f"  ⚠️  Key Vault access failed: {e}")
        return None


def test_environment_variables():
    """Test 1: Check environment variables or Key Vault access."""
    print("\n" + "="*60)
    print("Test 1: Checking API key (env var or Key Vault)...")
    print("="*60)
    
    # Default endpoint for East US 2
    DEFAULT_ENDPOINT = "https://deepprompteastus2.openai.azure.com"
    
    # Check for API key in environment
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    
    if api_key:
        display_value = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "****"
        print(f"  ✅ AZURE_OPENAI_API_KEY = {display_value} (from environment)")
    else:
        print(f"  ⚠️  AZURE_OPENAI_API_KEY not set, trying Key Vault...")
        api_key = get_api_key_from_keyvault()
        if api_key:
            display_value = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "****"
            print(f"  ✅ API key retrieved from Key Vault: {display_value}")
            # Set it for subsequent tests
            os.environ["AZURE_OPENAI_API_KEY"] = api_key
        else:
            print(f"  ❌ Could not retrieve API key from Key Vault")
            print(f"\n  Please either:")
            print(f"    1. Login to Azure: az login")
            print(f"    2. Or set: export AZURE_OPENAI_API_KEY='<your-key>'")
            return False
    
    # Check optional vars
    optional_vars = [
        ("AZURE_OPENAI_ENDPOINT", DEFAULT_ENDPOINT),
        ("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
    ]
    
    for var, default in optional_vars:
        value = os.getenv(var)
        if value:
            print(f"  ✅ {var} = {value}")
        else:
            print(f"  ⚠️  {var} not set (using default: {default})")
    
    print("\n✅ API key available!")
    return True


def test_config_loading():
    """Test 2: Load CABConfig and verify GPT-5.2 model."""
    print("\n" + "="*60)
    print("Test 2: Loading CAB configuration...")
    print("="*60)
    
    try:
        from cab_evaluation.core.config import CABConfig, ModelConfig
        
        config = CABConfig()
        print(f"  ✅ CABConfig loaded successfully")
        print(f"  ✅ Total models configured: {len(config.models)}")
        
        # List all models
        print("\n  Available models:")
        for name, model_config in config.models.items():
            print(f"    - {name}: provider={model_config.provider}, model_id={model_config.model_id}")
        
        return config
    except Exception as e:
        print(f"  ❌ Failed to load config: {e}")
        return None


def test_gpt52_model(config):
    """Test 3: Verify GPT-5.2 model exists and is configured for Azure."""
    print("\n" + "="*60)
    print("Test 3: Checking GPT-5.2 model configuration...")
    print("="*60)
    
    # Check for gpt-5.2 or gpt52
    gpt52_names = ["gpt-5.2", "gpt52"]
    found_model = None
    
    for name in gpt52_names:
        if name in config.models:
            found_model = config.models[name]
            print(f"  ✅ Found model: {name}")
            break
    
    if not found_model:
        print(f"  ❌ GPT-5.2 model not found in config")
        return False
    
    # Verify Azure configuration
    print(f"\n  Model details:")
    print(f"    - name: {found_model.name}")
    print(f"    - model_id: {found_model.model_id}")
    print(f"    - provider: {found_model.provider}")
    print(f"    - max_tokens: {found_model.max_tokens}")
    print(f"    - temperature: {found_model.temperature}")
    print(f"    - api_key_env_var: {found_model.api_key_env_var}")
    print(f"    - azure_endpoint_env_var: {found_model.azure_endpoint_env_var}")
    print(f"    - azure_api_version: {found_model.azure_api_version}")
    print(f"    - azure_deployment_name: {found_model.azure_deployment_name}")
    
    if found_model.provider != "azure_openai":
        print(f"  ❌ Model provider is not 'azure_openai'")
        return False
    
    print(f"\n  ✅ GPT-5.2 model is correctly configured for Azure OpenAI!")
    return True


def test_azure_client_creation():
    """Test 4: Create Azure OpenAI client."""
    print("\n" + "="*60)
    print("Test 4: Creating Azure OpenAI client...")
    print("="*60)
    
    # Default endpoint for East US 2
    DEFAULT_ENDPOINT = "https://deepprompteastus2.openai.azure.com"
    
    try:
        from openai import AzureOpenAI
        
        # API key should be set by test_environment_variables
        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        if not api_key:
            # Try Key Vault as fallback
            api_key = get_api_key_from_keyvault()
        
        if not api_key:
            print(f"  ❌ No API key available")
            return None
        
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", DEFAULT_ENDPOINT)
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
        
        # Ensure endpoint has https://
        if not endpoint.startswith("https://"):
            endpoint = f"https://{endpoint}"
        
        client = AzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=endpoint
        )
        
        print(f"  ✅ Azure OpenAI client created successfully")
        print(f"    - Endpoint: {endpoint}")
        print(f"    - API Version: {api_version}")
        
        return client
    except Exception as e:
        print(f"  ❌ Failed to create Azure OpenAI client: {e}")
        return None


def test_api_connectivity(client):
    """Test 5: Test API connectivity with a simple request."""
    print("\n" + "="*60)
    print("Test 5: Testing API connectivity...")
    print("="*60)
    
    try:
        # Make a minimal API call to test connectivity
        # GPT-5.2 uses max_completion_tokens instead of max_tokens
        response = client.chat.completions.create(
            model="gpt-5.2",  # Azure deployment name
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say 'API connection successful' in exactly 3 words."}
            ],
            max_completion_tokens=20,
            temperature=0
        )
        
        result = response.choices[0].message.content
        print(f"  ✅ API call successful!")
        print(f"  Response: {result}")
        
        return True
    except Exception as e:
        print(f"  ❌ API call failed: {e}")
        print(f"\n  This could be due to:")
        print(f"    - Invalid API key")
        print(f"    - Incorrect endpoint URL")
        print(f"    - Deployment name mismatch (expected 'gpt-5.2')")
        print(f"    - Network connectivity issues")
        return False


def test_llm_service():
    """Test 6: Test LLM service with Azure OpenAI."""
    print("\n" + "="*60)
    print("Test 6: Testing LLM Service integration...")
    print("="*60)
    
    try:
        from cab_evaluation.core.config import CABConfig
        from cab_evaluation.agents.llm_service import LLMService
        
        config = CABConfig()
        llm_service = LLMService(config)
        
        print(f"  ✅ LLM Service created successfully")
        
        # Get model config
        model_config = config.get_model_config("gpt-5.2")
        print(f"  ✅ Got model config for gpt-5.2")
        
        # Test Azure client creation
        azure_client = llm_service._get_azure_openai_client(model_config)
        print(f"  ✅ Azure OpenAI client retrieved from LLM service")
        
        return True
    except Exception as e:
        print(f"  ❌ LLM Service test failed: {e}")
        return False


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("  CodeAssistBench Azure OpenAI Configuration Tests")
    print("="*60)
    
    results = []
    
    # Test 1: Environment variables
    results.append(("Environment Variables", test_environment_variables()))
    
    if not results[-1][1]:
        print("\n" + "="*60)
        print("❌ Cannot continue without environment variables")
        print("="*60)
        return 1
    
    # Test 2: Config loading
    config = test_config_loading()
    results.append(("Config Loading", config is not None))
    
    if not config:
        print("\n❌ Cannot continue without config")
        return 1
    
    # Test 3: GPT-5.2 model
    results.append(("GPT-5.2 Model", test_gpt52_model(config)))
    
    # Test 4: Azure client creation
    client = test_azure_client_creation()
    results.append(("Azure Client Creation", client is not None))
    
    # Test 5: API connectivity (optional - only if client was created)
    if client:
        results.append(("API Connectivity", test_api_connectivity(client)))
    else:
        results.append(("API Connectivity", False))
    
    # Test 6: LLM Service integration
    results.append(("LLM Service Integration", test_llm_service()))
    
    # Summary
    print("\n" + "="*60)
    print("  TEST SUMMARY")
    print("="*60)
    
    passed = 0
    failed = 0
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {name}")
        if result:
            passed += 1
        else:
            failed += 1
    
    print(f"\n  Total: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("\n🎉 All tests passed! Azure OpenAI is configured correctly.")
        return 0
    else:
        print(f"\n⚠️  Some tests failed. Please check the configuration.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
