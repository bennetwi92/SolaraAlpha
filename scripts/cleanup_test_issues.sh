#!/bin/bash
# Cleanup script for orchestrator test issues

echo "================================================================================"
echo "Cleaning up orchestrator test issues"
echo "================================================================================"
echo ""

# Test issue numbers
TEST_ISSUES=(368 369 370 371)

echo "Closing test issues..."
for issue in "${TEST_ISSUES[@]}"; do
    echo "  Closing issue #${issue}..."
    gh issue close "$issue" --comment "Closing test issue - orchestrator validation complete" 2>/dev/null
    if [ $? -eq 0 ]; then
        echo "    ✅ Closed"
    else
        echo "    ⚠️  Already closed or doesn't exist"
    fi
done

echo ""
echo "================================================================================"
echo "Cleanup complete"
echo "================================================================================"
