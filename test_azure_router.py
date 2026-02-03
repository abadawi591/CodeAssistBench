#!/usr/bin/env python3
"""
Test script for Azure OpenAI Endpoint Router.

Tests multi-endpoint routing, failover, and load balancing.
"""

import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))


def test_router_initialization():
    """Test router initializes with all 4 endpoints."""
    print("\n" + "="*60)
    print("TEST 1: Router Initialization")
    print("="*60)
    
    try:
        from cab_evaluation.agents.azure_endpoint_router import (
            AzureEndpointRouter, 
            DEFAULT_ENDPOINTS,
            EndpointStatus
        )
        
        router = AzureEndpointRouter()
        
        print(f"✓ Router created with {len(router.endpoints)} endpoints")
        
        # Verify all 4 endpoints
        expected_names = ["eastus2-primary", "southcentralus", "swedencentral", "eastus2-secondary"]
        for i, endpoint in enumerate(router.endpoints):
            print(f"  {i+1}. {endpoint.name}")
            print(f"     URL: {endpoint.endpoint_url}")
            print(f"     Deployment: {endpoint.deployment_name}")
            print(f"     RPM: {endpoint.rpm_limit:,}")
            print(f"     Priority: {endpoint.priority}")
            print(f"     Weight: {endpoint.weight:.2f}")
            print(f"     Status: {endpoint.status.value}")
        
        # Check total capacity
        capacity = router.get_total_capacity()
        print(f"\n✓ Total capacity: {capacity['rpm']:,} RPM, {capacity['tpm']:,} TPM")
        
        assert len(router.endpoints) == 4, f"Expected 4 endpoints, got {len(router.endpoints)}"
        assert capacity['rpm'] == 30500, f"Expected 30,500 RPM, got {capacity['rpm']}"
        
        print("\n✓ Router initialization PASSED")
        return True
        
    except Exception as e:
        print(f"\n✗ Router initialization FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_endpoint_selection():
    """Test weighted endpoint selection."""
    print("\n" + "="*60)
    print("TEST 2: Endpoint Selection (Weighted)")
    print("="*60)
    
    try:
        from cab_evaluation.agents.azure_endpoint_router import AzureEndpointRouter
        
        router = AzureEndpointRouter()
        
        # Run 1000 selections and count distribution
        selections = {}
        for _ in range(1000):
            endpoint = router._select_endpoint()
            selections[endpoint.name] = selections.get(endpoint.name, 0) + 1
        
        print("Selection distribution (1000 samples):")
        for name, count in sorted(selections.items(), key=lambda x: -x[1]):
            pct = count / 10
            bar = "█" * int(pct / 2)
            print(f"  {name}: {count} ({pct:.1f}%) {bar}")
        
        # Verify higher priority endpoints get more requests
        assert selections.get("eastus2-primary", 0) > selections.get("eastus2-secondary", 0), \
            "Primary should get more requests than secondary"
        
        print("\n✓ Endpoint selection PASSED")
        return True
        
    except Exception as e:
        print(f"\n✗ Endpoint selection FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_api_connectivity():
    """Test actual API connectivity with failover."""
    print("\n" + "="*60)
    print("TEST 3: API Connectivity with Failover")
    print("="*60)
    
    try:
        from cab_evaluation.agents.azure_endpoint_router import AzureEndpointRouter
        
        router = AzureEndpointRouter()
        
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say 'Router test successful!' and nothing else."}
        ]
        
        print("Making API call with multi-endpoint routing...")
        
        response = await router.call_with_retry(
            messages=messages,
            max_completion_tokens=50,
            temperature=0.0
        )
        
        print(f"✓ Response: {response}")
        
        # Print router stats
        stats = router.get_stats()
        print(f"\nRouter stats:")
        print(f"  Total requests: {stats['total_requests']}")
        print(f"  Successful: {stats['successful_requests']}")
        print(f"  Failed: {stats['failed_requests']}")
        print(f"  Failovers: {stats['failovers']}")
        print(f"  Success rate: {stats['success_rate']:.1f}%")
        
        print("\n✓ API connectivity PASSED")
        return True
        
    except Exception as e:
        print(f"\n✗ API connectivity FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_concurrent_requests():
    """Test concurrent request handling."""
    print("\n" + "="*60)
    print("TEST 4: Concurrent Requests (10 parallel)")
    print("="*60)
    
    try:
        from cab_evaluation.agents.azure_endpoint_router import AzureEndpointRouter
        import time
        
        router = AzureEndpointRouter()
        
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Reply with exactly: 'OK'"}
        ]
        
        print("Sending 10 concurrent requests...")
        start_time = time.time()
        
        # Create 10 concurrent tasks
        tasks = [
            router.call_with_retry(
                messages=messages,
                max_completion_tokens=10,
                temperature=0.0
            )
            for _ in range(10)
        ]
        
        # Execute concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        elapsed = time.time() - start_time
        
        successful = sum(1 for r in results if not isinstance(r, Exception))
        failed = sum(1 for r in results if isinstance(r, Exception))
        
        print(f"✓ Completed in {elapsed:.2f}s")
        print(f"  Successful: {successful}/10")
        print(f"  Failed: {failed}/10")
        print(f"  Avg time per request: {elapsed/10:.2f}s (would be {elapsed}s sequential)")
        
        # Print endpoint distribution
        stats = router.get_stats()
        print(f"\nEndpoint usage:")
        for ep in stats['endpoints']:
            print(f"  {ep['name']}: {ep['requests_this_minute']} requests, status={ep['status']}")
        
        assert successful >= 8, f"Expected at least 8 successful requests, got {successful}"
        
        print("\n✓ Concurrent requests PASSED")
        return True
        
    except Exception as e:
        print(f"\n✗ Concurrent requests FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_llm_service_integration():
    """Test LLMService integration with router."""
    print("\n" + "="*60)
    print("TEST 5: LLMService Integration")
    print("="*60)
    
    try:
        from cab_evaluation.core.config import CABConfig
        from cab_evaluation.agents.llm_service import LLMService
        
        config = CABConfig()
        llm_service = LLMService(config, use_azure_router=True)
        
        # Check router is initialized
        assert llm_service._azure_router is not None, "Azure router not initialized"
        print(f"✓ LLMService created with Azure router")
        
        # Get model config
        model_config = config.get_model_config("gpt-5.2")
        print(f"✓ Model config: {model_config.name}, provider={model_config.provider}")
        
        # Make a test call
        response = await llm_service.call_model(
            user_prompt="Reply with exactly: 'Integration test OK'",
            system_prompt="You are a helpful assistant.",
            model_config=model_config,
            agent_type="test",
            issue_id="test-001"
        )
        
        print(f"✓ Response: {response}")
        
        # Get router stats
        stats = llm_service.get_azure_router_stats()
        if stats:
            print(f"✓ Router stats: {stats['successful_requests']} successful, "
                  f"{stats['failovers']} failovers")
        
        print("\n✓ LLMService integration PASSED")
        return True
        
    except Exception as e:
        print(f"\n✗ LLMService integration FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    print("="*60)
    print("AZURE OPENAI ENDPOINT ROUTER TEST SUITE")
    print("="*60)
    print("Testing multi-endpoint routing with 4 Azure OpenAI endpoints:")
    print("  1. East US 2 (Primary) - 10,000 RPM")
    print("  2. South Central US - 10,000 RPM")
    print("  3. Sweden Central - 8,500 RPM")
    print("  4. East US 2 (Secondary) - 2,000 RPM")
    print("  TOTAL: 30,500 RPM")
    
    results = []
    
    # Test 1: Router initialization
    results.append(("Router Initialization", test_router_initialization()))
    
    # Test 2: Endpoint selection
    results.append(("Endpoint Selection", test_endpoint_selection()))
    
    # Test 3: API connectivity
    results.append(("API Connectivity", await test_api_connectivity()))
    
    # Test 4: Concurrent requests
    results.append(("Concurrent Requests", await test_concurrent_requests()))
    
    # Test 5: LLMService integration
    results.append(("LLMService Integration", await test_llm_service_integration()))
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASSED" if result else "✗ FAILED"
        print(f"  {status}: {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
