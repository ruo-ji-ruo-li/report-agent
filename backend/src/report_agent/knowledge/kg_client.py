def check_neo4j(uri: str, user: str, password: str) -> bool:
    from neo4j import GraphDatabase

    with GraphDatabase.driver(uri, auth=(user, password)).session() as s:
        s.run("RETURN 1")
    return True
