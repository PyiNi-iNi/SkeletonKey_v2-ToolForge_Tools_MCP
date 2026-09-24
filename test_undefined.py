code = """def outer():
    print('start')
    
    def inner():
        return undefined_function()
    
    print('after inner def')
    return 42
"""

import ast
tree = ast.parse(code)
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'outer':
        print(f'AST: outer ends at line {node.end_lineno}')
        print(f'AST: Body statements: {len(node.body)}')

code_obj = compile(code, '<test>', 'exec')
for const in code_obj.co_consts:
    if hasattr(const, 'co_name') and const.co_name == 'outer':
        lines = list(const.co_lines())
        for start, end, lineno in lines:
            if lineno is not None:
                print(f'  addr {start}-{end}: line {lineno}')

# Execute
print('\\nExecuting:')
exec(code)