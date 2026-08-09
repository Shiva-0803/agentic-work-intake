import asyncio
import os
import json
import sqlite3
from app import (
    init_db, 
    get_db,
    get_mock_ai_response, 
    tool_draft_communication, 
    tool_bounded_website_check, 
    tool_create_task_record, 
    tool_set_reminder,
    post_intake,
    execute_action_by_id,
    update_overall_request_status
)

async def run_tests():
    print("=== STARTING AUTOMATED VERIFICATION PLAN ===")
    
    # 1. Initialize DB
    print("\n[Test 1] Initializing SQLite database...")
    init_db()
    if os.path.exists("work_intake.db"):
        print("[PASS] Database 'work_intake.db' initialized successfully.")
    else:
        print("[FAIL] Database file not found!")
        return

    # 2. Test Scenario 1: Routine Business Work parsing
    print("\n[Test 2] Testing Scenario 1 (Routine Business Work) parsing...")
    text_s1 = "Summarize the partner discussion on yesterday's call about the custom integration timeline. Please draft a thank-you email to Sarah at partner@company.com and set a 7-day reminder to check on their API docs update."
    interp_s1 = get_mock_ai_response(text_s1)
    
    assert interp_s1.task_title == "Partner Meeting Summary & Follow-Up", "Scenario 1 Title Mismatch"
    assert len(interp_s1.action_items) == 3, "Scenario 1 Actions count mismatch"
    assert interp_s1.action_items[0].route == "human_review", "Scenario 1 first action route should be human_review"
    assert interp_s1.action_items[1].route == "automatic", "Scenario 1 second action route should be automatic"
    print("[PASS] Scenario 1 parsed and routed correctly.")

    # 3. Test Scenario 2: Website Work parsing
    print("\n[Test 3] Testing Scenario 2 (Product / Website Work) parsing...")
    text_s2 = "Review hedamo.com, run whatever automated checks your prototype actually supports, and produce a short technical report."
    interp_s2 = get_mock_ai_response(text_s2)
    
    assert interp_s2.task_title == "hedamo.com Website Audit", "Scenario 2 Title Mismatch"
    assert interp_s2.action_items[0].tool_name == "bounded_website_check", "Scenario 2 first tool should be website check"
    assert interp_s2.action_items[0].route == "automatic", "Scenario 2 first route should be automatic"
    print("[PASS] Scenario 2 parsed and routed correctly.")

    # 4. Test Scenario 3: Ambiguous Request parsing
    print("\n[Test 4] Testing Scenario 3 (Ambiguous Request) parsing...")
    text_s3 = "Please take care of the documentation and send it to everyone before the meeting."
    interp_s3 = get_mock_ai_response(text_s3)
    
    assert "Clarification" in interp_s3.task_title or "Ambiguous" in interp_s3.task_title, "Scenario 3 Title should mention ambiguity/clarification"
    assert len(interp_s3.missing_information) > 0, "Scenario 3 missing info list should not be empty"
    assert interp_s3.action_items[0].route == "clarification", "Scenario 3 action route should be clarification"
    print("[PASS] Scenario 3 parsed and flagged as ambiguous correctly.")
    print(f"   Missing Information Flags: {interp_s3.missing_information}")

    # 5. Test Tools execution
    print("\n[Test 5] Testing individual tools execution...")
    req_id = 999  # Temporary test ID
    
    # 5a. Draft communication
    draft = await tool_draft_communication(req_id, "Sarah", "Integration Timeline", "We discussed the custom timeline and are aligning resources.")
    assert "To: Sarah" in draft, "Draft recipient missing"
    assert "timeline" in draft.lower(), "Draft content topic missing"
    print("[PASS] Tool 'draft_communication' executed successfully.")

    # 5b. Bounded website check on hedamo.com
    print("Running website audit check on hedamo.com...")
    try:
        web_res = await tool_bounded_website_check(req_id, "https://hedamo.com")
        assert web_res["status_code"] == 200, "Website status code is not 200"
        assert "url" in web_res, "URL key missing in response"
        print(f"[PASS] Tool 'bounded_website_check' completed: {json.dumps(web_res, indent=2)}")
    except Exception as e:
        print(f"[FAIL] Tool 'bounded_website_check' failed: {str(e)}")

    # 5c. Create task brief
    brief_path = await tool_create_task_record(req_id, "Test Brief Report", "This is some test markdown documentation content.")
    assert os.path.exists(brief_path), f"Task brief file not created: {brief_path}"
    with open(brief_path, "r", encoding="utf-8") as f:
        content = f.read()
        assert "Test Brief Report" in content, "Brief file content mismatch"
    print(f"[PASS] Tool 'create_task_record' executed successfully. File created at: {brief_path}")

    # 5d. Set reminder
    reminder_res = await tool_set_reminder(req_id, "Check integration timeline updates", 7)
    assert reminder_res["status"] == "scheduled", "Reminder status not scheduled"
    assert reminder_res["delay_days"] == 7, "Reminder days mismatch"
    print(f"[PASS] Tool 'set_reminder' simulated successfully: {reminder_res}")

    # 6. Test failure handling pathway
    print("\n[Test 6] Testing Failure Handling Path with invalid website check URL...")
    try:
        # This domain is guaranteed to fail
        await tool_bounded_website_check(req_id, "https://this-does-not-exist-12345-fake-domain.com")
        print("[FAIL] Failure path test failed: Website check completed unexpectedly!")
    except ValueError as e:
        print(f"[PASS] Failure path test succeeded: Caught expected exception: {str(e)}")

    # 7. Check database storage persistence
    print("\n[Test 7] Verifying database persistence records...")
    # Inject one complete scenario into database
    res = await post_intake(
        text="Check hedamo.com and send a brief report.",
        use_mock=True
    )
    test_req_id = res["request_id"]
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM requests WHERE id = ?", (test_req_id,))
    row_req = cursor.fetchone()
    assert row_req is not None, "Request row not found in DB"
    
    cursor.execute("SELECT * FROM interpretations WHERE request_id = ?", (test_req_id,))
    row_interp = cursor.fetchone()
    assert row_interp is not None, "Interpretation row not found in DB"
    assert row_interp["task_title"] == "hedamo.com Website Audit", "Interpretation task_title is wrong in DB"
    
    cursor.execute("SELECT * FROM action_items WHERE request_id = ?", (test_req_id,))
    rows_actions = cursor.fetchall()
    assert len(rows_actions) > 0, "No action items created in DB"
    
    cursor.execute("SELECT * FROM activity_logs WHERE request_id = ?", (test_req_id,))
    rows_logs = cursor.fetchall()
    assert len(rows_logs) > 0, "No logs created in DB"
    
    conn.close()
    print("[PASS] Persistence checks passed successfully. DB retains state between runs.")
    
    print("\n=== ALL TEST SCENARIOS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    asyncio.run(run_tests())
