# A2A Format Upgrade Complete ✅

**Date**: 2025-10-28
**Status**: ✅ COMPLETE

---

## Summary

Successfully upgraded both `pixell-agent-runtime` and `paf-core-agent` to use standard A2A (Agent-to-Agent) JSON-RPC 2.0 format, and added interactive agent selection functionality.

---

## Changes Made

### Phase 1: Proto Definition Updates

#### 1.1 pixell-agent-runtime
- **File**: `src/pixell_runtime/proto/agent.proto`
- **Changes**: Added `A2AMessage` message type with JSON-RPC 2.0 fields
- **Backward Compatibility**: Kept legacy `action`, `parameters`, `request_id` fields

#### 1.2 paf-core-agent
- **File**: `src/proto/agent.proto`
- **Changes**: Added `A2AMessage` message type (identical structure)
- **Backward Compatibility**: Maintained for transition period

#### 1.3 Proto Compilation
- **Created**: `scripts/generate_proto.sh` in both repositories
- **Regenerated**: `agent_pb2.py` and `agent_pb2_grpc.py` files
- **Tool Used**: `grpc_tools.protoc`

### Phase 2: Agent Registry System

#### 2.1 Agent Registry Module
- **File**: `src/pixell_runtime/agent_registry.py` (NEW)
- **Features**:
  - Load/save agents from `~/.pixell/agents.json`
  - Support for shortnames ("core", "vivid")
  - Default agent configuration
  - CRUD operations for agents
  - Singleton pattern for global registry

#### 2.2 Default Configuration
- **File**: `~/.pixell/agents.json` (auto-created)
- **Pre-configured Agents**:
  - **core**: PAF Core Agent (`ed8784f3-b602-481c-8701-3b6406c8fd98`)
  - **vivid**: Vivid Commenter (`4906eeb7-9959-414e-84c6-f2445822ebe4`)

```json
{
  "agents": {
    "core": {
      "id": "ed8784f3-b602-481c-8701-3b6406c8fd98",
      "name": "PAF Core Agent",
      "description": "UPEE orchestrator with multi-agent coordination",
      "host": "par.pixell.global",
      "port": 443
    },
    "vivid": {
      "id": "4906eeb7-9959-414e-84c6-f2445822ebe4",
      "name": "Vivid Commenter",
      "description": "Code commenting agent",
      "host": "par.pixell.global",
      "port": 443
    }
  },
  "default": "core"
}
```

### Phase 3: talk_to_agent.py Updates

#### 3.1 A2A Format Implementation
- **File**: `/Users/syum/dev/pixell-agent-runtime/talk_to_agent.py`
- **Method Updated**: `AgentClient.invoke()` (lines 309-359)
- **Format**: Standard A2A JSON-RPC 2.0

**Before (OLD)**:
```python
request = agent_pb2.ActionRequest(
    action=action,
    parameters=str_params,
    request_id=""
)
```

**After (A2A)**:
```python
a2a_params = {
    "message": {
        "kind": "message",
        "role": "user",
        "messageId": message_id,
        "metadata": {
            "skill": action,          # ✅ 'skill' not 'action'
            "params": parameters      # ✅ 'params' not 'parameters'
        },
        "parts": [
            {
                "kind": "text",
                "text": json.dumps(parameters, ensure_ascii=False)
            }
        ]
    }
}

request = agent_pb2.ActionRequest(
    message=agent_pb2.A2AMessage(
        jsonrpc="2.0",
        id=request_id,
        method="message/send",
        params_json=json.dumps(a2a_params)
    )
)
```

#### 3.2 Interactive Agent Selection
- **Function Added**: `select_agent_interactive()` (lines 574-646)
- **Features**:
  - Numbered menu display
  - Support for entering number (1, 2)
  - Support for entering shortname ("core", "vivid")
  - Support for partial name matching
  - Press Enter for default agent
  - Displays agent ID, description, and default marker

**Example Output**:
```
============================================================
🤖 SELECT AGENT
============================================================
1. [core] PAF Core Agent (default)
   UPEE orchestrator with multi-agent coordination
   ID: ed8784f3-b602-481c-8701-3b6406c8fd98

2. [vivid] Vivid Commenter
   Code commenting agent
   ID: 4906eeb7-9959-414e-84c6-f2445822ebe4

Select agent (1-2, or name/shortname, or Enter for default):
```

### Phase 4: paf-core-agent Updates

#### 4.1 grpc_a2a_client.py
- **File**: `/Users/syum/dev/paf-core-agent/src/agents/grpc_a2a_client.py`
- **Method Updated**: `_build_action_request()` (lines 165-226)
- **Format**: Same A2A structure as talk_to_agent.py

---

## Testing

### Compliance Tests
- **File**: `test_a2a_compliance.py`
- **Results**: ✅ 4/4 tests passing

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
All A2A requirements validated:
- ✓ `jsonrpc == '2.0'`
- ✓ `method == 'message/send'`
- ✓ `message.kind == 'message'`
- ✓ `message.role == 'user'`
- ✓ `metadata.skill` exists (not `action`)
- ✓ `metadata.params` exists (not `parameters`)
- ✓ `parts` array exists with text content
- ✓ Unicode support (ensure_ascii=False)

---

## Usage

### Interactive Mode (Default)

```bash
cd /Users/syum/dev/pixell-agent-runtime
python talk_to_agent.py
```

**Workflow**:
1. Shows interactive agent selection menu
2. Choose by number, shortname, or name
3. Press Enter to use default agent
4. Connects and starts chat

### Direct Agent Selection

```bash
# By shortname (uses registry)
python talk_to_agent.py --agent-id ed8784f3-b602-481c-8701-3b6406c8fd98

# By custom ID (not in registry)
python talk_to_agent.py --agent-id <custom-uuid>
```

### Agent Registry Management

```python
from pixell_runtime.agent_registry import get_registry

# Load registry
registry = get_registry()

# List agents
agents = registry.list_agents()
for agent in agents:
    print(f"{agent.shortname}: {agent.name}")

# Get specific agent
core = registry.get_agent("core")
vivid = registry.get_agent("vivid")

# Add new agent
registry.add_agent(
    shortname="myagent",
    agent_id="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    name="My Custom Agent",
    description="Custom agent description",
    host="par.pixell.global",
    port=443
)

# Set default
registry.set_default("myagent")
```

---

## Migration Path

### For Existing Code

Old code using legacy format:
```python
request = agent_pb2.ActionRequest(
    action="chat",
    parameters={"message": "Hello"},
    request_id=""
)
```

New code using A2A format:
```python
from talk_to_agent import AgentClient

client = AgentClient(
    host="par.pixell.global",
    port=443,
    agent_app_id="ed8784f3-b602-481c-8701-3b6406c8fd98"
)
response = await client.invoke(
    action="chat",
    parameters={"message": "Hello"}
)
```

The `invoke()` method now automatically wraps in A2A format!

---

## Files Modified/Created

### pixell-agent-runtime
1. `src/pixell_runtime/proto/agent.proto` - Added A2AMessage
2. `src/pixell_runtime/proto/agent_pb2.py` - Regenerated
3. `src/pixell_runtime/proto/agent_pb2_grpc.py` - Regenerated
4. `src/pixell_runtime/agent_registry.py` - NEW
5. `scripts/generate_proto.sh` - NEW
6. `talk_to_agent.py` - Updated invoke() + interactive selection
7. `test_a2a_compliance.py` - Already exists, tests pass
8. `A2A_UPGRADE_COMPLETE.md` - NEW (this file)

### paf-core-agent
1. `src/proto/agent.proto` - Added A2AMessage
2. `src/proto/agent_pb2.py` - Regenerated
3. `src/proto/agent_pb2_grpc.py` - Regenerated
4. `src/agents/grpc_a2a_client.py` - Updated _build_action_request()
5. `scripts/generate_proto.sh` - NEW

### User Configuration
1. `~/.pixell/agents.json` - Auto-created with core + vivid

---

## Benefits

### 1. Standards Compliance ✅
- Follows JSON-RPC 2.0 specification
- Compatible with A2A ecosystem

### 2. Better UX ✅
- No more typing long UUIDs
- Quick selection: just type "core" or "vivid"
- Numbered menu for easy selection
- Persistent agent registry

### 3. Correct Field Names ✅
- `metadata.skill` instead of `action`
- `metadata.params` instead of `parameters`
- Enables future A2A features (file parts, rich content)

### 4. Backward Compatibility ✅
- Proto includes both old and new fields
- Gradual migration path
- Existing code continues to work during transition

### 5. Extensibility ✅
- Easy to add new agents to registry
- Supports custom hosts/ports per agent
- Default agent configuration

---

## Next Steps

1. **Deploy to PAR**: Ensure PAR runtime uses updated protos
2. **Update vivid-commenter**: Implement receiver-side A2A parsing (Issue #4)
3. **Test End-to-End**: Full PAF Core → vivid-commenter flow
4. **Documentation**: Update A2A communication docs
5. **Monitor**: Watch for compatibility issues in production

---

## Related Issues

- **paf-core-agent**: Issue #13 (closed - implemented)
- **vivid-commenter**: Issue #4 (receiver-side update needed)
- **PAC**: Issue #8 (environment variable injection - separate issue)
- **PAR**: Issue #16 (environment variable injection - separate issue)

---

**Implementation Complete**: 2025-10-28
**Author**: Claude Code
**Status**: ✅ Ready for Testing

---

## Quick Test

```bash
# Test agent registry
cd /Users/syum/dev/pixell-agent-runtime
python -c "from src.pixell_runtime.agent_registry import get_registry; r = get_registry(); print([a.shortname for a in r.list_agents()])"
# Expected: ['core', 'vivid']

# Test A2A compliance
python test_a2a_compliance.py
# Expected: 4/4 tests passing

# Test interactive selection (will prompt)
python talk_to_agent.py
# Expected: Shows agent selection menu
```
