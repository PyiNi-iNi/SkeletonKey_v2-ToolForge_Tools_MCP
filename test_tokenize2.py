import tokenize
import io

with open('skeletonkey/core/catalog_tools.py', 'rb') as f:
    content = f.read()

tokens = list(tokenize.tokenize(io.BytesIO(content).readline))
# Find tokens around line 1336
for i, tok in enumerate(tokens):
    if tok.type in (4, 5, 6, 66, 65) and 1330 <= tok.start[0] <= 1370:
        print(f'{tokenize.tok_name[tok.type]:10} {tok.string!r:30} {tok.start} {tok.end}')