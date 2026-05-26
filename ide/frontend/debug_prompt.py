from pathlib import Path
text = Path('src/AiChat.js').read_text(encoding='utf-8')
idx = text.find('Use exactly one of these formats:')
print('idx=', idx)
print(repr(text[idx:idx+400]))
