#!/bin/bash

# Configuration: Target a specific component or scenario
# Usage: 
#   ./scripts/run_tests.sh                          # Run all tests
#   ./scripts/run_tests.sh rag                      # Run only RAG tests
#   ./scripts/run_tests.sh consultant               # Run only Consultant tests
#   ./scripts/run_tests.sh execution                # Run only Execution tests
#   ./scripts/run_tests.sh rejection                # Run only Rejection tests
#   ./scripts/run_tests.sh recreation               # Run code recreation tests
#   ./scripts/run_tests.sh all "L1_01"              # Run specific scenario by ID

TYPE=$1
FILTER=$2

case $TYPE in
    rag)        TARGET="tests/test_rag.py" ;;
    consultant) TARGET="tests/test_consultant.py" ;;
    execution)  TARGET="tests/test_execution.py" ;;
    rejection)  TARGET="tests/test_rejection.py" ;;
    recreation) TARGET="tests/test_recreation.py" ;;
    *)          TARGET="tests/" ;;
esac

  if [[ -z "${ONLY_NEW_SCENARIOS+x}" ]]; then
    export ONLY_NEW_SCENARIOS=1
    echo "ONLY_NEW_SCENARIOS not set. Defaulting to 1 (not testing old tests)."
  fi

  if [[ "$TYPE" == "rejection" && -z "${ONLY_NEW_REJECTION_SCENARIOS+x}" ]]; then
    export ONLY_NEW_REJECTION_SCENARIOS="${ONLY_NEW_SCENARIOS}"
    echo "ONLY_NEW_REJECTION_SCENARIOS not set. Mirroring ONLY_NEW_SCENARIOS=${ONLY_NEW_SCENARIOS}."
fi

if [[ -z "$FILTER" && "$TYPE" != "rag" && "$TYPE" != "consultant" && "$TYPE" != "execution" && "$TYPE" != "rejection" && "$TYPE" != "recreation" && "$TYPE" != "all" && -n "$TYPE" ]]; then
    TARGET="tests/"
    FILTER=$TYPE
fi

echo "Going to the project folder."
cd ~/izs-llm || exit 1

echo "Installing pytest and httpx in the container."
docker compose exec api pip install pytest httpx

echo "Starting the tests for: ${TARGET:-all} ${FILTER:+with filter: $FILTER}"

K_FLAG=""
if [ -n "$FILTER" ]; then
    K_FLAG="-k $FILTER"
fi

# No need for external networking setup!
docker compose exec \
  -e JUDGE_BASE_URL="${JUDGE_BASE_URL:-http://195.148.31.206:8000/v1}" \
  -e MISTRAL_API_KEY="${MISTRAL_API_KEY}" \
  -e ONLY_NEW_SCENARIOS="${ONLY_NEW_SCENARIOS}" \
  -e ONLY_NEW_REJECTION_SCENARIOS="${ONLY_NEW_REJECTION_SCENARIOS}" \
  api pytest "$TARGET" -v -s $K_FLAG -W ignore::DeprecationWarning

echo "Finished running tests."
