from skeletonkey.toolkit import build

tk = build()

# Load some integrations
tk.engine.call('integrations.load', {'name': 'simple-demo'})
tk.engine.call('integrations.load', {'name': 'sqlite'})
tk.engine.call('integrations.load', {'name': 'postgresql'})
tk.engine.call('integrations.load', {'name': 'mongodb'})
tk.engine.call('integrations.load', {'name': 'redis'})
tk.engine.call('integrations.load', {'name': 'aws-s3'})
tk.engine.call('integrations.load', {'name': 'requests'})
tk.engine.call('integrations.load', {'name': 'github'})
tk.engine.call('integrations.load', {'name': 'stripe'})

print("=== INTEGRATIONS (loaded) ===")
for name, inst in tk.integrations._instances.items():
    tools = inst.instance.get_tools()
    print(f"  {name}: {len(tools)} tools")
    for t in tools:
        print(f"    - {t['id']}: {t.get('title', '')} | risk={t.get('risk', '')} | tier={t.get('tier', '')}")

tk.close()