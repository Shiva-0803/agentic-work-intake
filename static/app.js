// State Management
let currentRequestId = null;
let activeRequestInterval = null;
let editingActionId = null;
let currentActions = [];

// DOM Elements
const requestList = document.getElementById("request-list");
const btnSubmit = document.getElementById("btn-submit");
const intakeText = document.getElementById("intake-text");
const detailCard = document.getElementById("detail-card");
const planCard = document.getElementById("plan-card");
const logConsole = document.getElementById("log-console");
const settingsToggle = document.getElementById("settings-toggle");
const configDropdown = document.getElementById("config-dropdown");
const modeSelect = document.getElementById("mode-select");
const realLlmSettings = document.getElementById("real-llm-settings");
const apiType = document.getElementById("api-type");
const apiKeyInput = document.getElementById("api-key");
const editModal = document.getElementById("edit-modal");
const editParamsForm = document.getElementById("edit-params-form");
const btnSaveEdit = document.getElementById("btn-save-edit");

// ---------------------------------------------------------
// Scenarios Definitions
// ---------------------------------------------------------
const scenarios = {
    1: `Summarize the partner discussion on yesterday's call about the custom integration timeline. Please draft a thank-you email to Sarah at partner@company.com and set a 7-day reminder to check on their API docs update.`,
    2: `Review hedamo.com, run whatever automated checks your prototype actually supports, and produce a short technical report.`,
    3: `Please take care of the documentation and send it to everyone before the meeting.`
};

function loadScenario(num) {
    intakeText.value = scenarios[num];
    addConsoleLog("SYSTEM", `Loaded Scenario ${num} into text intake area.`, "info");
}

// ---------------------------------------------------------
// Settings & Dropdown Handlers
// ---------------------------------------------------------
settingsToggle.addEventListener("click", () => {
    configDropdown.classList.toggle("hidden");
});

modeSelect.addEventListener("change", () => {
    // API settings are hidden in UI for security, loaded from backend .env
});

// Close settings if clicked outside
document.addEventListener("click", (e) => {
    if (!settingsToggle.contains(e.target) && !configDropdown.contains(e.target)) {
        configDropdown.classList.add("hidden");
    }
});

// ---------------------------------------------------------
// Logging Helpers
// ---------------------------------------------------------
function addConsoleLog(source, message, level = "info") {
    const timestamp = new Date().toLocaleTimeString();
    const logLine = document.createElement("div");
    logLine.className = `log-line ${level}`;
    logLine.innerHTML = `<span style="color: var(--text-muted)">[${timestamp}]</span> <strong>[${source}]</strong> ${message}`;
    logConsole.appendChild(logLine);
    logConsole.scrollTop = logConsole.scrollHeight;
}

function clearLogs() {
    logConsole.innerHTML = '<div class="log-line info">[System] Log console cleared.</div>';
}

// ---------------------------------------------------------
// Load Requests List (Sidebar)
// ---------------------------------------------------------
async function fetchRequestHistory() {
    try {
        const response = await fetch("/api/requests");
        const list = await response.json();
        
        if (list.length === 0) {
            requestList.innerHTML = '<div class="no-history">No intake requests yet.</div>';
            return;
        }
        
        requestList.innerHTML = "";
        list.forEach(req => {
            const dateStr = new Date(req.timestamp).toLocaleString();
            const title = req.task_title || "Unstructured Intake Request";
            
            const item = document.createElement("div");
            item.className = `request-item ${currentRequestId === req.id ? 'active' : ''}`;
            item.onclick = () => selectRequest(req.id);
            
            item.innerHTML = `
                <div class="item-title">${title}</div>
                <div class="item-snippet">${req.raw_text}</div>
                <div class="item-meta">
                    <span>ID: #${req.id}</span>
                    <span class="action-status-indicator">
                        <span class="status-badge-dot ${req.status}"></span>
                        <span>${formatStatus(req.status)}</span>
                    </span>
                </div>
            `;
            requestList.appendChild(item);
        });
    } catch (err) {
        console.error("Error loading requests history", err);
    }
}

function formatStatus(status) {
    return status.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

// ---------------------------------------------------------
// Submit Ingestion Text
// ---------------------------------------------------------
btnSubmit.addEventListener("click", async () => {
    const text = intakeText.value.trim();
    if (!text) {
        alert("Please enter unstructured text to ingest!");
        return;
    }
    
    btnSubmit.disabled = true;
    btnSubmit.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Ingesting & Analyzing...`;
    
    const useMock = modeSelect.value === "mock";
    const type = apiType.value;
    const key = apiKeyInput.value.trim();
    const smtpHost = document.getElementById("smtp-host").value.trim();
    const smtpPort = document.getElementById("smtp-port").value.trim();
    const smtpUser = document.getElementById("smtp-user").value.trim();
    const smtpPass = document.getElementById("smtp-pass").value.trim();
    
    // Persist SMTP configurations locally in browser
    saveSMTPSettings();
    
    const smtp_config = smtpUser && smtpPass ? {
        host: smtpHost || "smtp.gmail.com",
        port: smtpPort ? parseInt(smtpPort) : 587,
        username: smtpUser,
        password: smtpPass
    } : null;
    
    addConsoleLog("SYSTEM", "Initiating ingestion pipeline...", "info");
    
    try {
        const response = await fetch("/api/intake", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                text: text,
                use_mock: useMock,
                api_type: type,
                api_key: key,
                smtp_config: smtp_config
            })
        });
        
        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || "Server failed to process request.");
        }
        
        const result = await response.json();
        currentRequestId = result.request_id;
        
        addConsoleLog("SYSTEM", `Ingestion successful. Request ID: #${currentRequestId}`, "success");
        
        // Refresh sidebar and show details
        await fetchRequestHistory();
        await selectRequest(currentRequestId);
        
    } catch (err) {
        addConsoleLog("ERROR", `Ingestion pipeline failed: ${err.message}`, "error");
        alert(`Error: ${err.message}`);
    } finally {
        btnSubmit.disabled = false;
        btnSubmit.innerHTML = `<i class="fa-solid fa-wand-magic-sparkles"></i> Analyze & Plan Workflow`;
    }
});

// ---------------------------------------------------------
// Select and View Request Details
// ---------------------------------------------------------
async function selectRequest(id) {
    currentRequestId = id;
    
    // Highlight in list
    document.querySelectorAll(".request-item").forEach(item => {
        item.classList.remove("active");
    });
    // Add active class to corresponding element
    fetchRequestHistory(); // Refreshes styling dynamically
    
    // Clear any existing logs polling
    if (activeRequestInterval) {
        clearInterval(activeRequestInterval);
    }
    
    await refreshRequestDetails();
    
    // Set polling for updates if it is still executing/pending
    activeRequestInterval = setInterval(async () => {
        const res = await fetch(`/api/requests/${id}`);
        const data = await res.json();
        
        // Refresh logs console and actions details
        updateLogsDisplay(data.logs);
        updateActionItemsList(data.actions);
        
        // Update statuses
        document.getElementById("badge-status").innerText = formatStatus(data.status);
        document.getElementById("badge-status").className = `badge status-${data.status}`;
        
        if (data.status !== "pending_execution" && data.status !== "executing" && data.status !== "processing") {
            clearInterval(activeRequestInterval);
        }
    }, 1500);
}

async function refreshRequestDetails() {
    if (!currentRequestId) return;
    
    try {
        const response = await fetch(`/api/requests/${currentRequestId}`);
        const data = await response.json();
        
        // Show cards
        detailCard.classList.remove("hidden");
        planCard.classList.remove("hidden");
        
        // Basic Info
        document.getElementById("detail-title").innerText = data.interpretation.task_title || "Work Intake Request";
        document.getElementById("detail-summary").innerText = data.interpretation.summary || "Summarizing...";
        
        // Set badges
        const pBadge = document.getElementById("badge-priority");
        pBadge.innerText = `Priority: ${data.interpretation.priority}`;
        pBadge.className = `badge badge-priority-${data.interpretation.priority}`;
        
        const dBadge = document.getElementById("badge-deadline");
        dBadge.innerText = `Deadline: ${data.interpretation.deadline}`;
        
        const sBadge = document.getElementById("badge-status");
        sBadge.innerText = formatStatus(data.status);
        sBadge.className = `badge status-${data.status}`;
        
        // Raw text content
        document.getElementById("raw-text-content").innerText = data.raw_text;
        
        // Missing Information
        const missingBlock = document.getElementById("missing-info-block");
        const missingList = document.getElementById("missing-info-list");
        if (data.interpretation.missing_information && data.interpretation.missing_information.length > 0) {
            missingBlock.classList.remove("hidden");
            missingList.innerHTML = "";
            data.interpretation.missing_information.forEach(info => {
                const li = document.createElement("li");
                li.innerText = info;
                missingList.appendChild(li);
            });
        } else {
            missingBlock.classList.add("hidden");
        }
        
        // Automation potential lists
        const autoList = document.getElementById("automation-list");
        autoList.innerHTML = "";
        data.interpretation.what_could_be_automated.forEach(item => {
            const li = document.createElement("li");
            li.innerText = item;
            autoList.appendChild(li);
        });
        
        const confirmList = document.getElementById("confirmation-list");
        confirmList.innerHTML = "";
        data.interpretation.what_requires_human_confirmation.forEach(item => {
            const li = document.createElement("li");
            li.innerText = item;
            confirmList.appendChild(li);
        });
        
        // Update logs and actions
        updateLogsDisplay(data.logs);
        updateActionItemsList(data.actions);
        
    } catch (err) {
        console.error("Error refreshing details", err);
    }
}

function updateLogsDisplay(logs) {
    // Clear and reload log console for this specific request
    logConsole.innerHTML = "";
    if (logs.length === 0) {
        logConsole.innerHTML = '<div class="log-line info">[System] No activity logs for this request.</div>';
        return;
    }
    
    logs.forEach(log => {
        const time = new Date(log.timestamp).toLocaleTimeString();
        const line = document.createElement("div");
        line.className = `log-line ${log.level.toLowerCase()}`;
        line.innerHTML = `<span style="color: var(--text-muted)">[${time}]</span> <strong>[SYSTEM]</strong> ${log.message}`;
        logConsole.appendChild(line);
    });
    logConsole.scrollTop = logConsole.scrollHeight;
}

function updateActionItemsList(actions) {
    currentActions = actions;
    const container = document.getElementById("action-items-container");
    container.innerHTML = "";
    
    if (actions.length === 0) {
        container.innerHTML = '<div class="no-actions">No action items generated for this request.</div>';
        return;
    }
    
    actions.forEach(action => {
        const card = document.createElement("div");
        card.className = "action-card";
        
        let controlsHtml = "";
        if (action.status === "waiting_for_approval") {
            controlsHtml = `
                <div class="action-controls">
                    <button class="btn btn-sm btn-approve" onclick="approveAction(${action.id})"><i class="fa-solid fa-check"></i> Approve & Run</button>
                    <button class="btn btn-sm btn-edit" onclick="openEditModal(${action.id})"><i class="fa-solid fa-pen-to-square"></i> Edit Params</button>
                    <button class="btn btn-sm btn-reject" onclick="rejectAction(${action.id})"><i class="fa-solid fa-xmark"></i> Reject</button>
                </div>
            `;
        }
        
        // Output formatting
        let outputHtml = "";
        if (action.output) {
            let displayedOutput = action.output;
            
            // Check if output is a filepath to turn into click link
            if (action.tool_name === "create_task_record" && action.status === "completed") {
                const filename = action.output.split('/').pop().split('\\').pop();
                displayedOutput = `Report generated successfully.<br>
                <a href="/tasks/${filename}" target="_blank" class="btn btn-sm btn-edit" style="display: inline-flex; align-items: center; gap: 8px; margin-top: 8px; text-decoration: none;">
                    <i class="fa-solid fa-file-invoice"></i> View Report & Export PDF
                </a>`;
            } else if (action.output.startsWith("{") || action.output.startsWith("[")) {
                try {
                    // Prettify JSON output
                    const parsed = JSON.parse(action.output);
                    displayedOutput = `<pre style="font-family: var(--font-mono); font-size: 0.775rem;">${JSON.stringify(parsed, null, 2)}</pre>`;
                } catch(e) {}
            }
            
            outputHtml = `
                <div class="action-output-box">
                    <div class="action-output-header">
                        <span>Execution Output:</span>
                        <span class="status-completed"><i class="fa-solid fa-circle-check"></i> Success</span>
                    </div>
                    <div class="action-output-content">${displayedOutput}</div>
                </div>
            `;
        } else if (action.status === "failed") {
            outputHtml = `
                <div class="action-output-box" style="border-color: rgba(239, 68, 68, 0.3)">
                    <div class="action-output-header">
                        <span style="color: var(--color-rose)">Failure Details:</span>
                        <span class="status-failed"><i class="fa-solid fa-circle-xmark"></i> Failed</span>
                    </div>
                    <div class="action-output-content" style="color: var(--color-rose)">${action.output || 'Action execution aborted due to dependency failure.'}</div>
                </div>
            `;
        }
        
        card.innerHTML = `
            <div class="action-card-header">
                <span class="action-card-title">${action.title}</span>
                <span class="action-route-badge route-${action.route}">${action.route.replace('_', ' ')}</span>
            </div>
            <div class="action-desc">${action.description}</div>
            <div class="action-reason"><strong>Rationale:</strong> ${action.reason}</div>
            
            <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 6px;">
                <span class="action-status-indicator">
                    <span class="status-badge-dot ${action.status}"></span>
                    <span class="status-${action.status}">${formatStatus(action.status)}</span>
                </span>
            </div>
            
            ${controlsHtml}
            ${outputHtml}
        `;
        container.appendChild(card);
    });
}

function escapeJson(obj) {
    return JSON.stringify(obj).replace(/'/g, "&apos;").replace(/"/g, "&quot;");
}

// ---------------------------------------------------------
// Action Control Endpoints Execution
// ---------------------------------------------------------
function getClientConfig() {
    const smtpHost = document.getElementById("smtp-host").value.trim();
    const smtpPort = document.getElementById("smtp-port").value.trim();
    const smtpUser = document.getElementById("smtp-user").value.trim();
    const smtpPass = document.getElementById("smtp-pass").value.trim();
    
    if (smtpUser && smtpPass) {
        return {
            smtp_config: {
                host: smtpHost || "smtp.gmail.com",
                port: smtpPort ? parseInt(smtpPort) : 587,
                username: smtpUser,
                password: smtpPass
            }
        };
    }
    return null;
}

async function approveAction(actionId) {
    addConsoleLog("USER", `Approved Action #${actionId}. Initiating execution...`, "success");
    const clientConfig = getClientConfig();
    try {
        const res = await fetch(`/api/actions/${actionId}/approve`, { 
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ client_config: clientConfig })
        });
        if (res.ok) {
            await refreshRequestDetails();
        } else {
            alert("Failed to approve action.");
        }
    } catch(e) {
        console.error(e);
    }
}

async function rejectAction(actionId) {
    if(!confirm("Are you sure you want to reject this action item?")) return;
    addConsoleLog("USER", `Rejected Action #${actionId}.`, "warn");
    try {
        const res = await fetch(`/api/actions/${actionId}/reject`, { method: "POST" });
        if (res.ok) {
            await refreshRequestDetails();
        }
    } catch(e) {
        console.error(e);
    }
}

// ---------------------------------------------------------
// Dynamic Parameter Edit Modal
// ---------------------------------------------------------
function openEditModal(actionId) {
    const action = currentActions.find(a => a.id === actionId);
    if (!action) {
        alert("Action not found.");
        return;
    }
    
    editingActionId = actionId;
    editParamsForm.innerHTML = "";
    
    const toolName = action.tool_name;
    const currentArgs = typeof action.tool_args === "string" ? JSON.parse(action.tool_args) : (action.tool_args || {});
    
    // Inject form inputs based on the tool
    if (toolName === "draft_communication") {
        editParamsForm.innerHTML = `
            <div class="form-group">
                <label>Recipient Name:</label>
                <input type="text" name="recipient_name" value="${currentArgs.recipient_name || ''}" required>
            </div>
            <div class="form-group">
                <label>Recipient Email:</label>
                <input type="email" name="recipient_email" value="${currentArgs.recipient_email || ''}" required>
            </div>
            <div class="form-group">
                <label>Topic:</label>
                <input type="text" name="topic" value="${currentArgs.topic || ''}" required>
            </div>
            <div class="form-group">
                <label>Context / Message Specifics:</label>
                <textarea name="context" style="height: 100px; resize: vertical;" required>${currentArgs.context || ''}</textarea>
            </div>
        `;
    } else if (toolName === "create_task_record") {
        editParamsForm.innerHTML = `
            <div class="form-group">
                <label>Task Brief Title:</label>
                <input type="text" name="title" value="${currentArgs.title || ''}" required>
            </div>
            <div class="form-group">
                <label>Brief Content (Markdown):</label>
                <textarea name="content" style="height: 150px; resize: vertical;" required>${currentArgs.content || ''}</textarea>
            </div>
        `;
    } else if (toolName === "set_reminder") {
        editParamsForm.innerHTML = `
            <div class="form-group">
                <label>Reminder Topic:</label>
                <input type="text" name="topic" value="${currentArgs.topic || ''}" required>
            </div>
            <div class="form-group">
                <label>Delay Days:</label>
                <input type="number" name="delay_days" value="${currentArgs.delay_days || 7}" min="1" max="365" required>
            </div>
        `;
    } else if (toolName === "bounded_website_check") {
        editParamsForm.innerHTML = `
            <div class="form-group">
                <label>Website URL:</label>
                <input type="url" name="url" value="${currentArgs.url || ''}" required>
            </div>
        `;
    } else {
        editParamsForm.innerHTML = `<p>No editable parameters for tool: ${toolName}</p>`;
    }
    
    editModal.classList.remove("hidden");
}

function closeEditModal() {
    editModal.classList.add("hidden");
    editingActionId = null;
}

btnSaveEdit.addEventListener("click", async (e) => {
    e.preventDefault();
    if (!editingActionId) return;
    
    const actionId = editingActionId;
    const formData = new FormData(editParamsForm);
    const updatedArgs = {};
    formData.forEach((value, key) => {
        updatedArgs[key] = value;
    });
    
    addConsoleLog("USER", `Saved parameter changes for Action #${actionId} and authorized execution.`, "success");
    closeEditModal();
    
    // Save SMTP configurations locally in browser
    saveSMTPSettings();
    
    const clientConfig = getClientConfig();
    try {
        const response = await fetch(`/api/actions/${actionId}/edit`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                edited_args: updatedArgs,
                client_config: clientConfig
            })
        });
        
        if (response.ok) {
            await refreshRequestDetails();
        } else {
            alert("Failed to edit and run action.");
        }
    } catch(err) {
        console.error(err);
    }
});

// UI helper to toggle raw text dropdown
function toggleRawText() {
    const rawContent = document.getElementById("raw-text-content");
    rawContent.classList.toggle("hidden");
}

// ---------------------------------------------------------
// SMTP LocalStorage Helpers
// ---------------------------------------------------------
function loadSMTPSettings() {
    const host = localStorage.getItem("smtp_host");
    const port = localStorage.getItem("smtp_port");
    const user = localStorage.getItem("smtp_user");
    const pass = localStorage.getItem("smtp_pass");
    
    if (host) document.getElementById("smtp-host").value = host;
    if (port) document.getElementById("smtp-port").value = port;
    if (user) document.getElementById("smtp-user").value = user;
    if (pass) document.getElementById("smtp-pass").value = pass;
}

function saveSMTPSettings() {
    localStorage.setItem("smtp_host", document.getElementById("smtp-host").value.trim());
    localStorage.setItem("smtp_port", document.getElementById("smtp-port").value.trim());
    localStorage.setItem("smtp_user", document.getElementById("smtp-user").value.trim());
    localStorage.setItem("smtp_pass", document.getElementById("smtp-pass").value.trim());
}

// ---------------------------------------------------------
// Initial Load
// ---------------------------------------------------------
window.addEventListener("DOMContentLoaded", () => {
    // Override browser form persistence to ensure Real LLM is active
    modeSelect.value = "real";
    
    // Auto-load SMTP credentials from browser storage
    loadSMTPSettings();
    
    // Check URL parameters for verify-link redirects
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get("verified") === "true" && urlParams.get("email")) {
        const email = urlParams.get("email");
        localStorage.setItem("verified_email", email);
        // Clear query parameters from URL
        window.history.replaceState({}, document.title, window.location.pathname);
        
        // Open configuration dropdown
        configDropdown.classList.remove("hidden");
        addConsoleLog("SYSTEM", `Email ${email} verified via link click! SMTP configuration unlocked.`, "success");
    }
    
    // Update SMTP fields lock status based on verification
    updateSMTPFieldsLock();
    
    fetchRequestHistory();
});

// ---------------------------------------------------------
// Mail Authentication (OTP / Link) Logic & Auth Gate
// ---------------------------------------------------------
// Gate Elements
const authGateOverlay = document.getElementById("auth-gate-overlay");
const btnGateSendOtp = document.getElementById("btn-gate-send-otp");
const btnGateVerifyOtp = document.getElementById("btn-gate-verify-otp");
const gateEmail = document.getElementById("gate-email");
const gateOtp = document.getElementById("gate-otp");
const gateOtpGroup = document.getElementById("gate-otp-group");
const gateOtpSimTip = document.getElementById("gate-otp-simulation-tip");
const gateSimulatedOtpCode = document.getElementById("gate-simulated-otp-code");

// Helper to handle OTP dispatch requests
async function requestOTP(emailInputEl, sendBtnEl, otpGroupEl, simTipEl, simCodeEl) {
    const email = emailInputEl.value.trim();
    if (!email) {
        alert("Please enter a valid email address.");
        return false;
    }
    
    sendBtnEl.disabled = true;
    sendBtnEl.innerText = "Sending...";
    
    try {
        const res = await fetch("/api/auth/send-otp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email })
        });
        const data = await res.json();
        
        if (data.success) {
            otpGroupEl.classList.remove("hidden");
            if (data.otp_simulated) {
                simTipEl.classList.remove("hidden");
                simCodeEl.innerText = data.otp_simulated;
                addConsoleLog("SYSTEM", `OTP Verification Code sent to ${email} (Simulation Mode). Code: ${data.otp_simulated}`, "info");
            } else {
                simTipEl.classList.add("hidden");
                addConsoleLog("SYSTEM", `OTP Verification Code sent to ${email} via SMTP. Check your inbox.`, "info");
            }
            alert("Verification code has been sent!");
            return true;
        } else {
            alert("Failed to send OTP: " + data.message);
        }
    } catch (err) {
        console.error(err);
        alert("Error requesting verification code. Please check server logs.");
    } finally {
        sendBtnEl.disabled = false;
        sendBtnEl.innerText = "Send Verification OTP";
    }
    return false;
}

// Helper to handle OTP verification requests
async function verifyOTP(emailInputEl, otpInputEl, verifyBtnEl, callback) {
    const email = emailInputEl.value.trim();
    const otp = otpInputEl.value.trim();
    if (!email || !otp) {
        alert("Please enter email and verification code.");
        return;
    }
    
    verifyBtnEl.disabled = true;
    verifyBtnEl.innerText = "Verifying...";
    
    try {
        const res = await fetch("/api/auth/verify-otp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, otp })
        });
        const data = await res.json();
        
        if (res.ok && data.success) {
            localStorage.setItem("verified_email", email);
            callback(email);
        } else {
            alert("Verification failed: " + (data.detail || data.message || "Invalid code"));
        }
    } catch (err) {
        console.error(err);
        alert("Error verifying code.");
    } finally {
        verifyBtnEl.disabled = false;
        verifyBtnEl.innerText = "Verify & Access Portal";
    }
}

// Bind Gate Listeners
btnGateSendOtp.addEventListener("click", () => {
    requestOTP(gateEmail, btnGateSendOtp, gateOtpGroup, gateOtpSimTip, gateSimulatedOtpCode);
});
btnGateVerifyOtp.addEventListener("click", () => {
    verifyOTP(gateEmail, gateOtp, btnGateVerifyOtp, (email) => {
        gateOtpGroup.classList.add("hidden");
        addConsoleLog("SYSTEM", `User authenticated as ${email}. Access granted.`, "success");
        
        // Hide gate screen with fade transition
        authGateOverlay.style.opacity = "0";
        setTimeout(() => {
            authGateOverlay.classList.add("hidden");
        }, 500);
        
        // Unlock main layout SMTP inputs
        updateSMTPFieldsLock();
        alert("Verification successful! Welcome to the IntelliWork Portal.");
    });
});

// Bind Log Out Listener
document.getElementById("btn-logout").addEventListener("click", () => {
    localStorage.removeItem("verified_email");
    
    // Reset gate state variables
    gateOtp.value = "";
    gateOtpGroup.classList.add("hidden");
    
    // Relock inputs and show gate overlay
    updateSMTPFieldsLock();
    configDropdown.classList.add("hidden");
    
    addConsoleLog("SYSTEM", "User logged out. Portal locked.", "info");
    alert("You have logged out. Portal access is now locked.");
});

function updateSMTPFieldsLock() {
    const verifiedEmail = localStorage.getItem("verified_email");
    if (!verifiedEmail) {
        setSMTPFieldsDisabled(true);
        authGateOverlay.classList.remove("hidden");
        authGateOverlay.style.opacity = "1";
        return;
    }
    
    // Check status with backend
    fetch(`/api/auth/check-status?email=${encodeURIComponent(verifiedEmail)}`)
        .then(res => res.json())
        .then(data => {
            if (data.verified) {
                setSMTPFieldsDisabled(false);
                gateEmail.value = verifiedEmail;
                
                // Hide gate screen if verified
                authGateOverlay.classList.add("hidden");
            } else {
                setSMTPFieldsDisabled(true);
                localStorage.removeItem("verified_email");
                authGateOverlay.classList.remove("hidden");
                authGateOverlay.style.opacity = "1";
            }
        })
        .catch(err => {
            console.error("Error checking auth status:", err);
            setSMTPFieldsDisabled(true);
            authGateOverlay.classList.remove("hidden");
            authGateOverlay.style.opacity = "1";
        });
}

function setSMTPFieldsDisabled(disabled) {
    document.getElementById("smtp-host").disabled = disabled;
    document.getElementById("smtp-port").disabled = disabled;
    document.getElementById("smtp-user").disabled = disabled;
    document.getElementById("smtp-pass").disabled = disabled;
}
