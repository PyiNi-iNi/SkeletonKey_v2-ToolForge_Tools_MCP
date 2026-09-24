from skeletonkey.toolkit import build

tk = build()

# Get project tools
print("=== PROJECTS TOOLS ===")
projects = tk.projects.list_projects()
for p in projects:
    print(f"  {p.name}: {p.type} | {len(p.tools)} tools | {p.path}")
    if p.tools:
        for t in p.tools:
            print(f"    - {t}")

print("\n=== INTEGRATIONS ===")
for name, inst in tk.integrations._instances.items():
    tools = inst.instance.get_tools()
    print(f"  {name}: {len(tools)} tools")
    for t in tools:
        print(f"    - {t['id']}: {t.get('title', '')}")

tk.close()