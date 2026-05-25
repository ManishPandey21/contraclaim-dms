#!/usr/bin/env bash
set -euo pipefail

PRIMARY_HOST=${MONGO_PRIMARY_HOST:-mongo1:27017}
REPLICA_SET_NAME=${MONGODB_REPLICA_SET:-rs0}

echo "Waiting for MongoDB primary candidate at ${PRIMARY_HOST}"
until mongosh --host "${PRIMARY_HOST}" --quiet --eval "db.adminCommand('ping').ok" >/dev/null 2>&1; do
  sleep 2
done

echo "Ensuring replica set ${REPLICA_SET_NAME}"
mongosh --host "${PRIMARY_HOST}" --quiet <<'EOF'
const setName = process.env.MONGODB_REPLICA_SET || "rs0";
const config = {
  _id: setName,
  members: [
    { _id: 0, host: "mongo1:27017", priority: 2 },
    { _id: 1, host: "mongo2:27017", priority: 1 },
    { _id: 2, host: "mongo3:27017", priority: 1 }
  ]
};

try {
  const status = rs.status();
  print(`Replica set already initialized: ${status.set}`);
} catch (err) {
  const result = rs.initiate(config);
  printjson(result);
}
EOF

echo "Replica set initialization complete"
