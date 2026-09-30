import json

class MetadataHandler():
    """
        Get and validate input
    """

    def __init__(self, metadata_path: str):
        self.metadata_path = metadata_path
    
    @property
    def metadata(self) -> dict:
        with open(self.metadata_path, mode='r', encoding='utf-8') as f:
            return json.load(f)