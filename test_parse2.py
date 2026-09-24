code = """def test():
    try:
        pass
    except:
        pass

    print('a')
    print('b')
    print('c')
    # def foo(
    #     x: int = 1,
    # ) -> int:
    #     \"\"\"Docstring\"\"\"
    #     if True:
    #         pass
    #     return 1
    # 
    # def bar():
    #     pass

print('module level')
"""
import ast
tree = ast.parse(code)
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'test':
        print(f'Function ends at line {node.end_lineno}')
        for stmt in node.body:
            print(f'  Line {stmt.lineno}: {type(stmt).__name__}')
    elif isinstance(node, ast.FunctionDef):
        print(f'Module-level function: {node.name} at line {node.lineno}')