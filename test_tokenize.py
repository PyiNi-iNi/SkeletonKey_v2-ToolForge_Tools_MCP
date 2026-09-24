import tokenize
import io

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

tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
for tok in tokens:
    print(f'{tok.type:10} {tok.string!r:20} {tok.start} {tok.end}')