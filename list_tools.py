from skeletonkey.toolkit import build

tk = build()

# Get all tools
tools = []
for man in tk.engine.registry.all():
    tools.append({
        'id': man.id,
        'title': man.title,
        'risk': man.risk,
        'tier': man.tier,
        'capability': man.capability,
        'group': man.group,
        'source': man.source,
        'provider': man.provider,
        'tags': man.tags,
    })

print(f"Total tools: {len(tools)}")
for t in tools:
    print(f"  {t['id']:45} | risk={t['risk']:12} | tier={t['tier']:6} | group={t['group'] or '':15} | provider={t['provider'] or ''}")

tk.close()