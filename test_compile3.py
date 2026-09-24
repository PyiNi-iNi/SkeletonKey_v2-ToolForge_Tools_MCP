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
    #     \"\"\"Watch for catalog changes and auto-refresh.
    #     
    #     Uses filesystem watching to detect changes in tool directories and
    #     automatically re-index the catalog.
    #     \"\"\"
    #     if not _toolkit:
    #         return {"error": "Toolkit not available"}
    #     
    #     # Get watch roots from toolkit's project indexer
    #     watch_paths = getattr(_toolkit, 'projects', None)
    #     watch_paths = watch_paths.roots if watch_paths else []
    #     if not watch_paths:
    #         return {"error": "No watch paths configured"}
    #     
    #     try:
    #         import watchfiles
    #         return _watch_watchfiles(watch_paths, debounce, max_iterations)
    #     except ImportError:
    #         return _watch_polling(watch_paths, interval, debounce, max_iterations)

print('module level')
"""

import ast
tree = ast.parse(code)
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'test':
        print(f'AST: Function ends at line {node.end_lineno}')
        print(f'AST: Body statements: {len(node.body)}')
        for stmt in node.body:
            print(f'  Line {stmt.lineno}: {type(stmt).__name__}')

code_obj = compile(code, '<test>', 'exec')
for const in code_obj.co_consts:
    if hasattr(const, 'co_name') and const.co_name == 'test':
        print(f'\\nCode object: firstlineno={const.co_firstlineno}')
        lines = list(const.co_lines())
        for start, end, lineno in lines:
            if lineno is not None:
                print(f'  addr {start}-{end}: line {lineno}')