with open('skeletonkey/core/catalog_tools.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Add debug prints after each major section
new_content = content.replace(
    '        report["registered"].append("catalog.package")',
    '        report["registered"].append("catalog.package")\n    print("DEBUG: After catalog.package registration")'
).replace(
    '    def tool_catalog_watch(',
    'print("DEBUG: About to define tool_catalog_watch")\n    def tool_catalog_watch('
).replace(
    '# End of register_catalog_tools function\n    return report',
    'print("DEBUG: About to return report")\n    return report'
)

with open('skeletonkey/core/catalog_tools.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Added debug prints')