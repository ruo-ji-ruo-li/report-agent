def check_milvus(uri: str) -> bool:
    from pymilvus import MilvusClient

    client = MilvusClient(uri=uri, timeout=3)
    client.list_collections()  # 不可达会抛异常,由 _check 捕获
    return True
