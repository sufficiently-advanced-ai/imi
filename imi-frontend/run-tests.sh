#!/bin/bash
# Script to run the entity management tests for Issue #244

echo "Running Domain-Aware Entity Management UI Tests - Issue #244"
echo "============================================================"

echo "Installing dependencies..."
npm install

echo "Running component tests..."
npm test -- __tests__/issue-244_test_entity_components.tsx

echo "Running page tests..."
npm test -- __tests__/issue-244_test_entity_pages.tsx

echo "Running API client tests..."
npm test -- __tests__/issue-244_test_entity_api_client.tsx

echo "All tests completed!"