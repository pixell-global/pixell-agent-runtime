# A2A Format Update for talk_to_agent.py

## Summary

Updated `talk_to_agent.py` to use **standard A2A (Agent-to-Agent) JSON-RPC 2.0 format** for gRPC communication, ensuring compatibility with paf-core-agent and vivid-commenter.

---

## Changes Made

### 1. Added UUID Import

**File**: `talk_to_agent.py:11`

```python
import uuid  # Added for generating unique message IDs
```

### 2. Updated `invoke()` Method

**File**: `talk_to_agent.py:308-359`

**Before (Broken):**
```python
# Convert parameters to string dict (protobuf limitation)
str_params = {k: json.dumps(v) if not isinstance(v, str) else v
              for k, v in parameters.items()}

request = agent_pb2.ActionRequest(
    action=action,              # ❌ OLD: flat field
    parameters=str_params,       # ❌ OLD: flat field
    request_id=""               # ❌ OLD: flat field
)
```

**After (A2A Compliant):**
```python
# Build standard A2A (Agent-to-Agent) message structure
message_id = str(uuid.uuid4())
request_id = str(uuid.uuid4())

a2a_params = {
    "message": {
        "kind": "message",
        "role": "user",
        "messageId": message_id,
        "metadata": {
            "skill": action,          # ✅ Use 'skill' not 'action'
            "params": parameters      # ✅ Use 'params' not 'parameters'
        },
        "parts": [
            {
                "kind": "text",
                "text": json.dumps(parameters, ensure_ascii=False)
            }
        ]
    }
}

# Wrap in A2AMessage with JSON-RPC 2.0 structure
request = agent_pb2.ActionRequest(
    message=agent_pb2.A2AMessage(
        jsonrpc="2.0",
        id=request_id,
        method="message/send",
        params_json=json.dumps(a2a_params)
    )
)
```

### 3. Created Compliance Test Suite

**File**: `test_a2a_compliance.py`

Comprehensive test suite validating:
- JSON-RPC 2.0 structure
- Correct field names (`skill` not `action`, `params` not `parameters`)
- `parts` array presence
- Complex parameter handling
- Unicode support

---

## Format Comparison

### Old Format (Broken ❌)
```json
{
  "action": "chat",
  "parameters": {
    "message": "Hello"
  },
  "request_id": "uuid"
}
```

### New A2A Format (Correct ✅)
```json
{
  "jsonrpc": "2.0",
  "id": "b36b456d-a7b8-4362-9e64-86e7e05e9e2e",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "role": "user",
      "messageId": "cc56f9d8-77b7-4d52-a84b-83eecd155c25",
      "metadata": {
        "skill": "chat",
        "params": {
          "message": "Hello"
        }
      },
      "parts": [
        {
          "kind": "text",
          "text": "{\"message\": \"Hello\"}"
        }
      ]
    }
  }
}
```

---

## Test Results

### Compliance Tests

```
🧪 A2A Message Structure Compliance Tests
======================================================================

✅ PASS: Chat action builds valid A2A message
✅ PASS: Comment action builds valid A2A message
✅ PASS: Complex parameters build valid A2A message
✅ PASS: Format exactly matches A2A specification

Passed: 4/4
```

### Validation Checks

All checks passed:
- ✓ `jsonrpc == '2.0'`
- ✓ `method == 'message/send'`
- ✓ `message.kind == 'message'`
- ✓ `message.role == 'user'`
- ✓ `metadata.skill` exists
- ✓ `metadata.params` exists
- ✓ `parts` array exists
- ✓ Parts have text content
- ✓ No legacy field names (`action`, `parameters`)

---

## Integration Testing Plan

### Phase 1: Local Testing ✅ COMPLETE
- [x] Create compliance test suite
- [x] Validate A2A message structure
- [x] Test with various action types
- [x] Test with complex parameters

### Phase 2: Agent Integration Testing

#### Test with paf-core-agent

```bash
# Start paf-core-agent locally (if not already running)
cd /Users/syum/dev/paf-core-agent
./scripts/start.sh

# Test A2A communication
cd /Users/syum/dev/pixell-agent-runtime
python talk_to_agent.py \
  --host localhost \
  --port 50051 \
  --agent-id paf-core-agent \
  --verbose
```

**Expected**: Successful health check and chat interaction

#### Test with vivid-commenter (PAR-deployed)

```bash
python talk_to_agent.py \
  --agent-id 4906eeb7-9959-414e-84c6-f2445822ebe4 \
  --dns-resolver native \
  --verbose
```

**Expected**: Successful interaction once vivid-commenter receiver is updated

### Phase 3: End-to-End Testing

Test scenarios:
1. **Chat conversation**: Natural language queries
2. **Code commenting**: Code analysis requests
3. **Complex parameters**: Nested data structures
4. **Unicode content**: International characters
5. **Error handling**: Invalid requests

---

## Compatibility Matrix

| Component | Status | Notes |
|-----------|--------|-------|
| **talk_to_agent.py** | ✅ Updated | Now sends A2A format |
| **paf-core-agent** | ✅ Compatible | Accepts A2A format (commit f6378d3) |
| **vivid-commenter** | ⚠️  Needs Update | Requires receiver-side fix ([issue #4](https://github.com/pixell-global/vivid-commenter/issues/4)) |
| **PAR (runtime)** | ✅ Compatible | Proto updated to A2A format |

---

## Benefits

- ✅ **Standards Compliance**: Follows JSON-RPC 2.0 specification
- ✅ **Interoperability**: Works with all A2A-compliant agents
- ✅ **File Transfer Support**: `parts` array enables file/rich content
- ✅ **Future-Proof**: Compatible with ecosystem evolution
- ✅ **Better Structure**: Clear separation of skill vs parameters
- ✅ **Traceability**: Unique message IDs for debugging

---

## Migration Notes

### Breaking Changes
- Old `action`/`parameters`/`request_id` fields no longer exist in proto
- Must use new `A2AMessage` wrapper structure

### Backward Compatibility
- None - this is a clean break from old format
- All clients must update to new format
- Proto regeneration required after changes

### Deployment Strategy
1. Update PAR proto definitions
2. Regenerate proto Python files
3. Update all A2A clients (talk_to_agent.py, etc.)
4. Update all A2A servers (vivid-commenter, etc.)
5. Test end-to-end communication

---

## Next Steps

1. **Deploy to PAR**: Ensure PAR runtime has updated protos
2. **Update vivid-commenter**: Implement receiver-side A2A parsing
3. **Integration Test**: Test full PAF Core → vivid-commenter flow
4. **Documentation**: Update A2A communication docs
5. **Monitor**: Watch for any compatibility issues in production

---

## Files Modified

1. `/Users/syum/dev/pixell-agent-runtime/talk_to_agent.py`
   - Added uuid import
   - Updated `invoke()` method to build A2A format

2. `/Users/syum/dev/pixell-agent-runtime/test_a2a_compliance.py` (new)
   - Comprehensive A2A format validation tests

---

## References

- **Issue**: https://github.com/pixell-global/paf-core-agent/issues/13
- **PAF Core Commit**: f6378d3 (A2A format implementation)
- **vivid-commenter Issue**: https://github.com/pixell-global/vivid-commenter/issues/4
- **A2A Specification**: JSON-RPC 2.0 with A2A extensions

---

**Date**: 2025-10-28
**Author**: Claude Code
**Status**: ✅ Implementation Complete, Ready for Integration Testing
