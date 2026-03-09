import json
import re
import logging
import aiohttp

from astrbot.api import logger

def strip_html(html_content):
    if not html_content:
        return ""
    # Simple regex strip to avoid bs4 dependency if only for this
    # But bs4 is already installed and used in main.py, so we can keep using it if we want,
    # or just use regex for lighter weight.
    # Since main.py imports BeautifulSoup, we can use it, but here let's stick to simple or use bs4 if imported.
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, 'html.parser')
    return soup.get_text()

async def fetch_hextech_data_from_url(url="https://apexlol.info/assets/chunks/data.Bq-2u7uT.js"):
    """
    Fetches Hextech data from the given JS file URL.
    Parses the JS object 'Wi' and 'Oi' using Python regex and string manipulation.
    """
    logger.info(f"Fetching Hextech data from {url}...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                response.raise_for_status()
                content = await response.text()
    except Exception as e:
        logger.error(f"Failed to download Hextech data: {e}")
        return None

    try:
        # Extract Wi array
        # Pattern: Wi=[ ... ]; or Wi=[ ... ],
        # We look for Wi=[ and then find the matching closing bracket ]
        
        wi_start_marker = "Wi=["
        wi_start = content.find(wi_start_marker)
        if wi_start == -1:
            logger.error("Could not find 'Wi=[' in the data file.")
            return None
        
        # Find the end of Wi array. Since it contains objects, we need to balance brackets or just find the next variable declaration/export.
        # Assuming the structure is relatively clean minified JS.
        # It usually ends with `],` or `];`
        # Let's simple find the first `],` or `];` after wi_start, BUT we must be careful about nested arrays (unlikely in this top level list of objects)
        # Actually, let's just use regex to capture the content inside `Wi=[ ... ]`
        
        # Better strategy: Find `Wi=[` and iterate to find matching `]`.
        wi_end = -1
        bracket_count = 0
        in_string = False
        string_char = ''
        
        # Start scanning from the opening bracket of Wi=[
        scan_start = wi_start + len("Wi=")
        
        for i in range(scan_start, len(content)):
            char = content[i]
            
            # Handle strings to ignore brackets inside them
            if in_string:
                if char == string_char and content[i-1] != '\\': # Simple check for escaped quote
                    in_string = False
                continue
            
            if char == '"' or char == "'":
                in_string = True
                string_char = char
                continue
                
            if char == '[':
                bracket_count += 1
            elif char == ']':
                bracket_count -= 1
                if bracket_count == 0:
                    wi_end = i + 1
                    break
        
        if wi_end == -1:
            logger.error("Could not parse Wi array bounds.")
            return None
            
        wi_js_content = content[scan_start:wi_end] # This is `[{...}, ...]`
        
        # Extract Oi object
        oi_start_marker = "Oi={"
        oi_start = content.find(oi_start_marker)
        oi_data = {}
        
        if oi_start != -1:
            # Similar extraction for Oi
            oi_end = -1
            brace_count = 0
            in_string = False
            string_char = ''
            
            scan_start_oi = oi_start + len("Oi=")
            
            for i in range(scan_start_oi, len(content)):
                char = content[i]
                if in_string:
                    if char == string_char and content[i-1] != '\\':
                        in_string = False
                    continue
                if char == '"' or char == "'":
                    in_string = True
                    string_char = char
                    continue
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        oi_end = i + 1
                        break
            
            if oi_end != -1:
                oi_js_content = content[scan_start_oi:oi_end] # `{...}`
                oi_data = _parse_js_object_to_dict(oi_js_content)

        # Parse Wi
        wi_data = _parse_js_array_to_list(wi_js_content)
        
        # Merge Oi mechanism into Wi
        # Oi is a dict where keys are IDs
        for item in wi_data:
            hex_id = item.get("id")
            if hex_id and hex_id in oi_data:
                mech = oi_data[hex_id].get("mechanism")
                if mech:
                    item["mechanism"] = mech
                    
        return wi_data

    except Exception as e:
        logger.error(f"Error parsing Hextech data: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

def _parse_js_object_to_dict(js_str):
    """
    Parses a JS object string `{key:val, ...}` to a Python dict.
    Uses regex to quote keys and handles basic values.
    """
    # 1. Quote unquoted keys: `key:` -> `"key":`
    # We need to be careful not to match inside strings.
    # A simple regex `(\w+):` might match inside strings if we are not careful.
    # But for this specific data, keys are simple identifiers.
    
    # Let's clean up the string first
    js_str = js_str.strip()
    
    # Replace keys. Valid JS keys here are alphanumeric.
    # Look for (start of string or char that isn't quote) followed by key followed by :
    # It's safer to just regex replace specific known keys if possible, or use a robust pattern.
    # Known keys in this data: id, key, name, title, roles, imageName, tier, description, icon, source, wikiKey, mechanism, zh, en, dmg_dealt, dmg_taken, tenacity
    
    # We can try a generic approach: `(?<=[{,])\s*(\w+):` matches keys after { or ,
    # But values can contain anything.
    
    # Let's try to make it JSON compliant.
    # 1. Quote keys
    # 2. Convert single quotes to double quotes? The data uses double quotes mostly, but some might be single.
    # The data example: `name:{zh:"...",en:"..."}`
    
    # Regex to quote keys:
    # Match a word followed by a colon, NOT inside quotes.
    # This is hard with regex alone.
    
    # Simpler approach: This data is specific.
    # Keys: `id:`, `tier:`, `name:`, `zh:`, `en:`, `description:`, `icon:`, `source:`, `wikiKey:`, `mechanism:`
    # We can just iterate these known keys and replace them.
    
    known_keys = [
        "id", "tier", "name", "zh", "en", "description", "icon", 
        "source", "wikiKey", "mechanism", "dmg_dealt", "dmg_taken", 
        "tenacity", "type", "plaintext", "gold", "base", "purchasable", 
        "total", "sell", "stats", "attention"
    ]
    
    # Sort by length desc to avoid partial replacement issues (though unlikely here)
    known_keys.sort(key=len, reverse=True)
    
    # We will use a tokenizing approach or just careful replacement.
    # Since we don't have a JS parser, and regex replacement is risky on full string.
    
    # Let's use `demjson3` logic but simplified:
    # 1. Replace `!0` with `true`, `!1` with `false` (common minification)
    js_str = js_str.replace("!0", "true").replace("!1", "false")
    
    # 2. Quote keys.
    # Pattern: `(\w+):` -> `"\1":`
    # We will use a regex that matches `key:` but ignores if it's inside a string.
    # Actually, simpler: The file is minified, so no spaces usually. `name:{`
    # We can loop through known keys and replace `key:` with `"key":`.
    # But we must ensure it's a key. i.e. preceded by `{` or `,`.
    
    for key in known_keys:
        # Replace `{key:` with `{"key":`
        js_str = js_str.replace(f"{{{key}:", f'{{"{key}":')
        # Replace `,key:` with `,"key":`
        js_str = js_str.replace(f",{key}:", f',"{key}":')
        
    # Also handle numerical keys if any (like in Oi: `15:{...}`)
    # Replace `,{number}:` or `{{number}:`
    js_str = re.sub(r'(?<=[{,])(\d+):', r'"\1":', js_str)
    
    # 3. Handle Template Literals (backticks) if any.
    # The example data showed: `description:{zh:'...',en:`...`}`
    # We need to convert backticks to double quotes and escape newlines/quotes inside.
    # This is getting complicated.
    
    # Alternative: The `fetch_hextech_data.py` used `node`.
    # The user instruction says "High risk security vulnerability (RCE) ... downloading JS script ... and executing with node".
    # So we MUST NOT use node.
    
    # Given the complexity of parsing arbitrary JS object string (template literals, nested objects, single/double quotes),
    # doing it with simple regex is prone to error.
    # However, for this specific task, we might get away with it if the data is consistent.
    
    # Let's try to handle strings carefully.
    
    # Function to parse a JS value.
    # It's better to use `demjson3` if installed, but we can't rely on it.
    # `chompjs` is another option.
    
    # Let's try a best-effort "dirty" parser since we know the structure.
    
    # Handle backticks: replace ` with " and escape " inside?
    # No, backticks allow newlines. JSON strings don't (unless escaped).
    
    # Let's rely on the fact that `json.loads` is strict. 
    # Maybe we can just strip the variable assignment and use `ast.literal_eval`? 
    # No, JS syntax != Python syntax (true/false/null vs True/False/None).
    
    # Helper to clean JS string to JSON
    def clean_js_to_json(text):
        # 1. Quote keys (alphanumeric start)
        # Use regex with lookbehind to ensure it follows { or ,
        text = re.sub(r'(?<=[{,])([a-zA-Z0-9_]+):', r'"\1":', text)
        
        # 2. Convert 'string' to "string"
        # This is hard because of potential ' inside string.
        # But if we assume standard JSON-like structure...
        
        # 3. Convert template literals `...` to "..."
        # Regex to find `...` and replace.
        def replace_backticks(match):
            content = match.group(1)
            # Escape " inside content
            content = content.replace('"', '\\"')
            # Escape newlines
            content = content.replace('\n', '\\n')
            return f'"{content}"'
            
        text = re.sub(r'`([^`]*)`', replace_backticks, text)
        
        # 4. Handle '...' strings
        # Only replace outer ' if it looks like a string.
        # This is risky.
        
        # 5. Keywords
        text = text.replace("!0", "true").replace("!1", "false")
        
        # Python's `eval` can handle dicts if we map true->True etc.
        # But `eval` is dangerous if input is malicious (RCE).
        # But here input is from a specific URL. If URL is hijacked, `eval` is bad.
        # `ast.literal_eval` is safer but doesn't support `true`/`false`.
        
        return text

    # Let's try a safer custom parser using a stack, but that's code heavy.
    # Given constraints, let's look at the data again.
    # `description:{zh:'...',en:`...`}`
    
    # If we use `ast.literal_eval`, we need to:
    # 1. Quote keys.
    # 2. Use ' or " for strings (Python supports both).
    # 3. Handle `true`/`false`/`null`.
    # 4. Handle backticks (convert to strings).
    
    # Let's try to sanitize for `ast.literal_eval`:
    cleaned = js_str
    
    # Keywords
    cleaned = cleaned.replace("!0", "True").replace("!1", "False")
    cleaned = cleaned.replace("true", "True").replace("false", "False").replace("null", "None")
    
    # Backticks to triple quotes (Python supports multiline strings with triple quotes)
    # `string` -> """string"""
    # But we need to escape `"""` inside if any.
    cleaned = re.sub(r'`([^`]*)`', lambda m: '"""' + m.group(1).replace('"""', '\\"\\"\\"') + '"""', cleaned)
    
    # Keys
    # {key: -> {"key":
    # ,key: -> ,"key":
    for key in known_keys:
        cleaned = cleaned.replace(f"{{{key}:", f'{{"{key}":')
        cleaned = cleaned.replace(f",{key}:", f',"{key}":')
    
    # Numerical keys
    cleaned = re.sub(r'(?<=[{,])(\d+):', r'"\1":', cleaned)
    
    # Remove trailing commas
    cleaned = re.sub(r',(\s*[}\]])', r'\1', cleaned)
    
    try:
        import ast
        return ast.literal_eval(cleaned)
    except Exception as e:
        logger.warning(f"Failed to parse with literal_eval: {e}. Data snippet: {cleaned[:100]}")
        return {}

def _parse_js_array_to_list(js_str):
    # Similar to object parser but for array
    return _parse_js_object_to_dict(js_str) # literal_eval handles lists too

