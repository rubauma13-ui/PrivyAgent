import os
import zipfile

src_dir = r"c:\Users\srmra\Downloads\files (10)\privyagent-integrated\privyagent"
zip_path = r"c:\Users\srmra\Downloads\privyagent-github-ready.zip"

def should_exclude(path, filename):
    # Exclude .env files and API keys
    if filename.startswith(".env") and filename != ".env.example":
        return True
    
    # Exclude pycache and node_modules
    parts = path.split(os.sep)
    if "__pycache__" in parts or "node_modules" in parts or ".git" in parts or ".pytest_cache" in parts:
        return True
    
    # Exclude built extension files (dist) since they're generated
    if "extension" in parts and "dist" in parts:
        return True
        
    # Exclude zip files, python bytecode, etc.
    if filename.endswith(".pyc") or filename.endswith(".zip"):
        return True
        
    return False

with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
    for root, dirs, files in os.walk(src_dir):
        for file in files:
            if not should_exclude(root, file):
                file_path = os.path.join(root, file)
                # Ensure the path in the zip is relative to the privyagent folder
                rel_path = os.path.relpath(file_path, src_dir)
                # Keep the base folder name 'privyagent' in the zip
                zipf.write(file_path, os.path.join("privyagent", rel_path))

print(f"Clean ZIP created successfully at: {zip_path}")
