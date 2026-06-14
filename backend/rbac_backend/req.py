import chardet

input_file = 'requirements.md'
output_file = 'requirements_utf8.txt'

# Detect encoding
with open(input_file, 'rb') as f:
    raw_data = f.read()
    result = chardet.detect(raw_data)
    current_encoding = result['encoding']

# Convert to UTF-8
with open(input_file, 'r', encoding=current_encoding) as f:
    text = f.read()

with open(output_file, 'w', encoding='utf-8') as f:
    f.write(text)
