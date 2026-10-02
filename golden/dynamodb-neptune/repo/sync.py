import os
from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
from gremlin_python.process.anonymous_traversal import traversal

def handler(event, context):
    conn = DriverRemoteConnection(os.environ["NEPTUNE_ENDPOINT"], "g")
    g = traversal().with_remote(conn)
    for record in event["Records"]:
        if record["eventName"] == "REMOVE":
            continue
        image = record["dynamodb"]["NewImage"]
        acct = image["account_id"]["S"]
        g.V().has("Account", "id", acct).fold().coalesce(
            __.unfold(), __.addV("Account").property("id", acct)
        ).next()
