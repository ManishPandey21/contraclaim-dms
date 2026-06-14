#!/usr/bin/env python
"""
Properly diagnose FalkorDB connection and configuration.
"""

import os
import subprocess
import sys

def diagnose_falkordb():
    print("=== FalkorDB Diagnosis ===")
    
    # 1. Check if container is running
    print("\n1. Checking container status...")
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "falkordb", "--format", "json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and "falkordb" in result.stdout:
            print("✓ FalkorDB container is running")
            
            # Get container status details
            result = subprocess.run(
                ["docker", "compose", "ps", "falkordb"],
                capture_output=True, text=True
            )
            print(f"Container status:\n{result.stdout}")
        else:
            print("✗ FalkorDB container is not running")
            return False
    except Exception as exc:
        print(f"✗ Could not check container status: {exc}")
        return False
    
    # 2. Check FalkorDB service configuration
    print("\n2. Checking FalkorDB configuration in docker-compose...")
    try:
        result = subprocess.run(
            ["docker", "compose", "config"],
            capture_output=True, text=True
        )
        if "falkordb" in result.stdout:
            # Extract falkordb service config
            lines = result.stdout.split('\n')
            in_falkordb = False
            for line in lines:
                if line.strip().startswith("falkordb:"):
                    in_falkordb = True
                elif in_falkordb and line.strip() and not line.startswith(' '):
                    break
                elif in_falkordb:
                    print(f"  {line.rstrip()}")
        else:
            print("  FalkorDB service not found in compose file")
    except Exception as exc:
        print(f"✗ Could not read compose config: {exc}")
    
    # 3. Test different connection methods
    print("\n3. Testing connections...")
    test_connections()
    
    return True

def test_connections():
    """Test various connection methods to FalkorDB"""
    import redis
    
    connection_tests = [
        # Try without password first (most common default)
        {"host": "localhost", "port": 6380, "password": None, "desc": "No password"},
        {"host": "localhost", "port": 6380, "password": None, "desc": "Port 6380, no password"},
        {"host": "falkordb", "port": 6380, "password": None, "desc": "Service name, no password"},
        
        # Try with default password
        {"host": "localhost", "port": 6380, "password": "default_password", "desc": "Default password"},
        {"host": "localhost", "port": 6380, "password": "default_password", "desc": "Port 6380, default password"},
        
        # Try empty password
        {"host": "localhost", "port": 6380, "password": "", "desc": "Empty password"},
    ]
    
    successful_connections = []
    
    for test in connection_tests:
        print(f"  Testing: {test['desc']}...")
        try:
            client = redis.Redis(
                host=test["host"],
                port=test["port"],
                password=test["password"],
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3
            )
            
            # Test basic connection
            client.ping()
            print(f"    ✓ Basic connection successful")
            
            # Test graph commands
            try:
                result = client.execute_command("GRAPH.LIST")
                print(f"    ✓ Graph commands available")
                test["graph"] = True
            except redis.ResponseError as e:
                print(f"    ⚠ Graph commands failed: {e}")
                test["graph"] = False
            except Exception as e:
                print(f"    ⚠ Graph test error: {e}")
                test["graph"] = False
            
            successful_connections.append(test)
            client.close()
            
        except redis.AuthenticationError as e:
            print(f"    ✗ Authentication failed")
        except redis.ConnectionError as e:
            print(f"    ✗ Connection failed")
        except Exception as e:
            print(f"    ✗ Failed: {e}")
    
    # Print successful connections
    if successful_connections:
        print(f"\n✓ Successful connections:")
        for conn in successful_connections:
            graph_status = "✓" if conn.get("graph") else "⚠"
            print(f"  {conn['desc']} - Graph: {graph_status}")
        
        # Test query on first successful connection
        test_query(successful_connections[0])
    else:
        print(f"\n❌ No successful connections")
        check_container_logs()

def test_query(connection):
    """Test actual graph queries on a successful connection"""
    print(f"\n4. Testing queries on: {connection['desc']}")
    
    import redis
    try:
        client = redis.Redis(
            host=connection["host"],
            port=connection["port"],
            password=connection["password"],
            decode_responses=True,
            socket_connect_timeout=5
        )
        
        # Test 1: List graphs
        print("  Testing GRAPH.LIST...")
        try:
            graphs = client.execute_command("GRAPH.LIST")
            print(f"    Available graphs: {graphs}")
        except Exception as e:
            print(f"    ✗ GRAPH.LIST failed: {e}")
        
        # Test 2: Count all nodes
        print("  Testing node count...")
        try:
            result = client.execute_command("GRAPH.QUERY", "G", "MATCH (n) RETURN COUNT(n)")
            if result and len(result) >= 2:
                count = result[1][0][0] if result[1] else 0
                print(f"    Total nodes: {count}")
            else:
                print(f"    No nodes found or unexpected format: {result}")
        except Exception as e:
            print(f"    ✗ Node count failed: {e}")
        
        # Test 3: Count letters
        print("  Testing letter count...")
        try:
            result = client.execute_command("GRAPH.QUERY", "G", "MATCH (l:Letter) RETURN COUNT(l)")
            if result and len(result) >= 2:
                count = result[1][0][0] if result[1] else 0
                print(f"    Letter nodes: {count}")
            else:
                print(f"    No letters found")
        except Exception as e:
            print(f"    ✗ Letter count failed: {e}")
        
        # Test 4: Sample schema
        print("  Testing schema...")
        try:
            result = client.execute_command("GRAPH.QUERY", "G", "CALL db.labels()")
            if result and len(result) >= 2:
                labels = [row[0] for row in result[1]] if result[1] else []
                print(f"    Node labels: {labels}")
        except Exception as e:
            print(f"    ✗ Schema check failed: {e}")
        
        client.close()
        
    except Exception as e:
        print(f"    ✗ Query test failed: {e}")

def check_container_logs():
    """Check FalkorDB container logs for clues"""
    print("\n5. Checking container logs...")
    try:
        result = subprocess.run(
            ["docker", "compose", "logs", "falkordb", "--tail=10"],
            capture_output=True, text=True
        )
        if result.stdout:
            print("Recent logs:")
            for line in result.stdout.split('\n')[-10:]:
                if line.strip():
                    print(f"  {line}")
    except Exception as e:
        print(f"  Could not get logs: {e}")

if __name__ == "__main__":
    diagnose_falkordb()