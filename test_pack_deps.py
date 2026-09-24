from skeletonkey.integrations.packs import IntegrationPackManager
import tempfile
import json
import os

# Create two test packs: base-pack and dependent-pack
with tempfile.TemporaryDirectory() as tmpdir:
    # Create base-pack
    base_pack_dir = os.path.join(tmpdir, 'base-pack')
    os.makedirs(base_pack_dir)
    
    base_manifest = {
        'schema_version': '1.0',
        'name': 'base-pack',
        'version': '1.0.0',
        'title': 'Base Pack',
        'description': 'Base pack with a simple integration',
        'integrations': [],
        'dependencies': []
    }
    with open(os.path.join(base_pack_dir, 'pack.json'), 'w') as f:
        json.dump(base_manifest, f)
    
    integration_code = '''from skeletonkey.integrations import Integration, IntegrationConfig, IntegrationType, IntegrationCategory, register_integration

@register_integration(
    name="base-pack-integration",
    title="Base Pack Integration",
    version="1.0.0",
    integration_type=IntegrationType.SDK,
    category=IntegrationCategory.DEVTOOLS,
)
class BasePackIntegration(Integration):
    def initialize(self): return True
    def cleanup(self): pass
    def get_tools(self): return []
'''
    with open(os.path.join(base_pack_dir, 'base_integration.py'), 'w') as f:
        f.write(integration_code)
    
    # Create dependent-pack
    dep_pack_dir = os.path.join(tmpdir, 'dependent-pack')
    os.makedirs(dep_pack_dir)
    
    dep_manifest = {
        'schema_version': '1.0',
        'name': 'dependent-pack',
        'version': '1.0.0',
        'title': 'Dependent Pack',
        'description': 'Pack that depends on base-pack',
        'integrations': [],
        'dependencies': [
            {'name': 'base-pack', 'version': '>=1.0.0', 'optional': False}
        ]
    }
    with open(os.path.join(dep_pack_dir, 'pack.json'), 'w') as f:
        json.dump(dep_manifest, f)
    
    dep_integration_code = '''from skeletonkey.integrations import Integration, IntegrationConfig, IntegrationType, IntegrationCategory, register_integration

@register_integration(
    name="dependent-pack-integration",
    title="Dependent Pack Integration",
    version="1.0.0",
    integration_type=IntegrationType.SDK,
    category=IntegrationCategory.DEVTOOLS,
)
class DependentPackIntegration(Integration):
    def initialize(self): return True
    def cleanup(self): pass
    def get_tools(self): return []
'''
    with open(os.path.join(dep_pack_dir, 'dependent_integration.py'), 'w') as f:
        f.write(dep_integration_code)
    
    # Install base-pack first
    mgr = IntegrationPackManager(install_root=os.path.join(tmpdir, 'installed'))
    result1 = mgr.install(base_pack_dir, 'local')
    print('Install base-pack result:', result1)
    
    # Install dependent-pack (should auto-install base-pack if not present)
    result2 = mgr.install(dep_pack_dir, 'local')
    print('Install dependent-pack result:', result2)
    
    print('Installed packs:', [(p.name, p.version) for p in mgr.list_installed()])