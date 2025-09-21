#!/bin/bash
# Test build script for Pixell Runtime

set -e

echo "=== Testing Pixell Runtime Build ==="
echo

# Set Python path
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"

# Step 1: Check Python version
echo "1. Checking Python version..."
python --version
echo

# Step 2: Verify all Python files compile
echo "2. Compiling Python files..."
find src -name "*.py" -type f | while read -r file; do
    python -m py_compile "$file"
done
echo "✓ All Python files compile successfully"
echo

# Step 3: Test imports
echo "3. Testing module imports..."
python -c "
import pixell_runtime
from pixell_runtime.core import models, config, exceptions
from pixell_runtime.main import create_app
print('✓ Core modules import successfully')
"
echo

# Step 4: Create and test FastAPI app
echo "4. Testing FastAPI application..."
python -c "
from pixell_runtime.main import create_app
app = create_app()
print('✓ FastAPI app created successfully')
print(f'  - App title: {app.title}')
print(f'  - App version: {app.version}')
"
echo

# Step 5: Check for required directories
echo "5. Verifying project structure..."
for dir in src/pixell_runtime/{core,api,registry,agents,metrics,utils} tests/{unit,integration} scripts docs; do
    if [ -d "$dir" ]; then
        echo "✓ Directory exists: $dir"
    else
        echo "✗ Missing directory: $dir"
        exit 1
    fi
done
echo

echo "=== Build test completed successfully! ==="