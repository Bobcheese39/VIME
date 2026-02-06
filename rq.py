
import pathlib
for f in ['plugin/vime.vim', 'python/vime_server.py']:
    p = pathlib.Path(f)
    content = p.read_bytes()
    content = content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    p.write_bytes(content)
    print(f'{f}: converted ({len(content)} bytes)')
