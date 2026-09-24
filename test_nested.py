code = """def outer():
    print('start')
    
    def inner(
        x: int = 1,
    ) -> int:
        \"\"\"Inner function docstring.
        
        Multi-line docstring.
        \"\"\"
        return x
    
    print('after inner def')
    return 42
"""

import ast
tree = ast.parse(code)
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'outer':
        print(f'AST: outer ends at line {node.end_lineno}')
        print(f'AST: Body statements: {len(node.body)}')
        for stmt in node.body:
            print(f'  Line {stmt.lineno}: {type(stmt).__name__}')

code_obj = compile(code, '<test>', 'exec')
for const in code_obj.co_consts:
    if hasattr(const, 'co_name') and const.co_name == 'outer':
        print(f'\\nCode object: firstlineno={const.co_firstlineno}')
        lines = list(const.co_lines())
        for start, end, lineno in lines:
            if lineno is not None:
                print(f'  addr {start}-{end}: line {lineno}')

# Execute
print('\\nExecuting:')
exec(code)