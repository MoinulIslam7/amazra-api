#!/bin/sh
set -e

until curl -s http://elasticsearch:9200 >/dev/null; do
  echo "Waiting for Elasticsearch..."
  sleep 2
done

curl -s -X PUT "http://elasticsearch:9200/_index_template/products-template" \
  -H "Content-Type: application/json" \
  -d @/setup/index-template.json >/dev/null

echo "Elasticsearch index template applied."
