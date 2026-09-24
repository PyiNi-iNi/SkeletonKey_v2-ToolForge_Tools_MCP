code = """def test():
    print('a')
    # def foo():
    #     \"\"\"Docstring\"\"\"
    #     if True:
    #         pass
    #     return 1
    print('b')
    return 2
"""
import ast
tree = ast.parse(code)
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'test':
        print(f'Function ends at line {node.end_lineno}')
        for stmt in node.body:
            print(f'  Line {stmt.lineno}: {type(stmt).__name__}')