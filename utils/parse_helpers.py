import json, re

def extract_first_json(text):
    start = text.find('{')
    if start == -1: return None
    stack = []
    for i,ch in enumerate(text[start:], start):
        if ch=='{': stack.append('{')
        elif ch=='}':
            if not stack: return None
            stack.pop()
            if not stack: return text[start:i+1]
    return None

def basic_sanitize(s):
    s = re.sub(r'\bNone\b','null',s)
    s = re.sub(r'\bTrue\b','true',s)
    s = re.sub(r'\bFalse\b','false',s)
    s = re.sub(r',\s*([}\]])', r'\1', s)
    s = re.sub(r',\s*null\s*:\s*("?null"?)','',s)
    s = re.sub(r"'",'"',s)
    return s

def quick_parse_and_validate(raw_text, fast_schema=None):
    block = extract_first_json(raw_text)
    if not block: return ('parse_failed','no_json_found')
    try:
        obj = json.loads(block)
    except Exception:
        try:
            obj = json.loads(basic_sanitize(block))
        except Exception as e:
            return ('parse_failed', str(e))
        else:
            return ('sanitized_ok', obj)
    return ('ok', obj)
