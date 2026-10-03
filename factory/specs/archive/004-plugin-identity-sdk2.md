---
id: 004-plugin-identity-sdk2
title: PluginIdentity + Plugin SDK v2 + plugins list/validate/doctor
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_plugins.py -q
---

# Context
Allowlist checks check.id and check.__class__.__name__ — docs promise
distribution names work. No API versioning for plugins.

# Acceptance Criteria
- PluginIdentity(distribution, version, api_version, entry_point); attached as check.__fd_identity__
- allowlist matches check.id OR identity.distribution OR entry-point name; never __class__.__name__
- PluginDescriptor(name, version, api_version, requires_forge_doctor_data, capabilities, checks) supported in same entry-point group
- api_version "2" supported; incompatible descriptors rejected with clear error
- `forge-doctor-data plugins` becomes group: default list (with API/Status cols), `plugins validate`, `plugins doctor`
