// ════════════════════════════════════════════════════════════════
//  DOM References
// ════════════════════════════════════════════════════════════════

const fileInput = document.getElementById('file-input');
const dropZone = document.getElementById('drop-zone');
const fileDetails = document.getElementById('file-details');
const fileNameDisplay = document.getElementById('file-name');
const processBtn = document.getElementById('process-btn');
const resetBtn = document.getElementById('reset-btn');
const consoleOutput = document.getElementById('console-output');

const uploadSection = document.getElementById('upload-section');
const dashboardSection = document.getElementById('dashboard-section');

const jobFilename = document.getElementById('job-filename');
const currentStatusText = document.getElementById('current-status-text');
const statusIndicator = document.getElementById('status-indicator');
const progressBarFill = document.getElementById('progress-bar-fill');
const progressText = document.getElementById('progress-text');
const statTotal = document.getElementById('stat-total');
const statSuccess = document.getElementById('stat-success');
const statSkipped = document.getElementById('stat-skipped');
const statErrors = document.getElementById('stat-errors');
const startFromInput = document.getElementById('start-from');

// Process Controls
const pauseBtn = document.getElementById('pause-btn');
const stopBtn = document.getElementById('stop-btn');
const cancelBtn = document.getElementById('cancel-btn');

// Login Prompt Modal
const promptModal = document.getElementById('ui-prompt-modal');
const promptText = document.getElementById('ui-prompt-text');
const promptContinueBtn = document.getElementById('ui-prompt-continue-btn');

// Batch Name Modal
const batchNameModal = document.getElementById('batch-name-modal');
const batchNameInput = document.getElementById('batch-name-input');
const batchNameConfirmBtn = document.getElementById('batch-name-confirm-btn');

// Tabs
const tabRunBtn = document.getElementById('tab-run-btn');
const tabSettingsBtn = document.getElementById('tab-settings-btn');
const viewRun = document.getElementById('view-run');
const viewSettings = document.getElementById('view-settings');

// Config Form
const configFieldsContainer = document.getElementById('config-fields-container');
const saveConfigBtn = document.getElementById('save-config-btn');
const configSaveStatus = document.getElementById('config-save-status');

// ════════════════════════════════════════════════════════════════
//  Application State
// ════════════════════════════════════════════════════════════════

let selectedFile = null;
let eventSource = null;
let isPaused = false;
let batchName = '';

// Stats
let totalRecords = 0;
let successCount = 0;
let skipCount = 0;
let errorCount = 0;

// ════════════════════════════════════════════════════════════════
//  Tab Switching
// ════════════════════════════════════════════════════════════════

tabRunBtn.addEventListener('click', () => {
    tabRunBtn.classList.add('active');
    tabSettingsBtn.classList.remove('active');
    viewRun.style.display = 'block';
    viewSettings.style.display = 'none';
});

tabSettingsBtn.addEventListener('click', () => {
    tabSettingsBtn.classList.add('active');
    tabRunBtn.classList.remove('active');
    viewSettings.style.display = 'block';
    viewRun.style.display = 'none';
    loadConfig();
});

// ════════════════════════════════════════════════════════════════
//  Config Management
// ════════════════════════════════════════════════════════════════

async function loadConfig() {
    try {
        const res = await fetch('/config');
        const data = await res.json();
        if (data.status === 'success') {
            configFieldsContainer.innerHTML = '';
            for (const [key, value] of Object.entries(data.config)) {
                const group = document.createElement('div');
                group.className = 'config-group';

                const label = document.createElement('label');
                // Format label: "recipient_name" → "Recipient Name"
                label.textContent = key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());

                let input;
                if (key.includes('address')) {
                    input = document.createElement('textarea');
                    input.rows = 4;
                    input.style.resize = 'vertical';
                } else {
                    input = document.createElement('input');
                    input.type = 'text';
                }

                input.className = 'input-field';
                input.id = 'cfg-' + key;
                input.value = value;

                group.appendChild(label);
                group.appendChild(input);
                configFieldsContainer.appendChild(group);
            }
        }
    } catch (e) {
        console.error("Failed to load config", e);
    }
}

saveConfigBtn.addEventListener('click', async () => {
    const inputs = configFieldsContainer.querySelectorAll('.input-field');
    const newConfig = {};
    inputs.forEach(input => {
        const key = input.id.replace('cfg-', '');
        newConfig[key] = input.value;
    });

    try {
        const res = await fetch('/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newConfig)
        });
        const data = await res.json();
        if (data.status === 'success') {
            configSaveStatus.style.display = 'inline';
            setTimeout(() => { configSaveStatus.style.display = 'none'; }, 2000);
        }
    } catch (e) {
        alert("Failed to save settings: " + e);
    }
});

// ════════════════════════════════════════════════════════════════
//  File Upload (Drag & Drop + Click)
// ════════════════════════════════════════════════════════════════

['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, preventDefaults, false);
});
function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}
['dragenter', 'dragover'].forEach(eventName => {
    dropZone.addEventListener(eventName, () => dropZone.classList.add('dragover'), false);
});
['dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, () => dropZone.classList.remove('dragover'), false);
});
dropZone.addEventListener('drop', handleDrop, false);

function handleDrop(e) {
    const dt = e.dataTransfer;
    const files = dt.files;
    handleFiles(files);
}

fileInput.addEventListener('change', function() {
    handleFiles(this.files);
});

function handleFiles(files) {
    if (files.length > 0) {
        const file = files[0];
        if (file.name.endsWith('.xlsx') || file.name.endsWith('.zip')) {
            selectedFile = file;
            fileNameDisplay.textContent = file.name;
            dropZone.style.display = 'none';
            fileDetails.style.display = 'flex';
        } else {
            alert('Please upload a valid .xlsx or .zip file.');
        }
    }
}

// ════════════════════════════════════════════════════════════════
//  Batch Name Prompt
// ════════════════════════════════════════════════════════════════

function showBatchNamePrompt() {
    // Suggest a default batch name based on current month/year
    const now = new Date();
    const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                        'July', 'August', 'September', 'October', 'November', 'December'];
    const defaultName = monthNames[now.getMonth()] + '_' + now.getFullYear();
    batchNameInput.value = defaultName;
    batchNameInput.placeholder = defaultName;
    batchNameModal.style.display = 'flex';
    batchNameInput.focus();
    batchNameInput.select();
}

batchNameConfirmBtn.addEventListener('click', () => {
    const name = batchNameInput.value.trim();
    if (!name) {
        batchNameInput.style.borderColor = 'var(--error)';
        return;
    }
    batchNameInput.style.borderColor = '';
    batchName = name;
    batchNameModal.style.display = 'none';
    startProcessing();
});

// Allow Enter key in batch name input
batchNameInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        batchNameConfirmBtn.click();
    }
});

// ════════════════════════════════════════════════════════════════
//  Processing Controls
// ════════════════════════════════════════════════════════════════

function resetUI() {
    dashboardSection.style.display = 'none';
    uploadSection.style.display = 'block';
    resetBtn.style.display = 'none';

    dropZone.style.display = 'block';
    fileDetails.style.display = 'none';
    selectedFile = null;
    fileInput.value = '';
    batchName = '';

    totalRecords = 0;
    successCount = 0;
    skipCount = 0;
    errorCount = 0;
    updateProgressUI();
    statTotal.textContent = '--';

    isPaused = false;
    pauseBtn.textContent = 'Pause';
    pauseBtn.disabled = false;
    stopBtn.disabled = false;
    cancelBtn.disabled = false;
    promptModal.style.display = 'none';
    batchNameModal.style.display = 'none';
}

resetBtn.addEventListener('click', resetUI);

pauseBtn.addEventListener('click', async () => {
    isPaused = !isPaused;
    const cmd = isPaused ? 'PAUSE' : 'RUN';
    pauseBtn.textContent = isPaused ? 'Resume' : 'Pause';

    await fetch('/state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: cmd })
    });
});

cancelBtn.addEventListener('click', async () => {
    if (confirm("Are you sure you want to cancel? This will stop the process and delete all downloaded files for this batch.")) {
        cancelBtn.disabled = true;
        stopBtn.disabled = true;
        pauseBtn.disabled = true;

        await fetch('/state', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: 'CANCEL' })
        });

        setStatus('Cancelling and cleaning up...', 'warning', true);
    }
});

stopBtn.addEventListener('click', async () => {
    if (confirm("Are you sure you want to stop? You can resume from where you left off next time.")) {
        stopBtn.disabled = true;
        cancelBtn.disabled = true;
        pauseBtn.disabled = true;

        await fetch('/state', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: 'STOP' })
        });

        setStatus('Stopping gracefully...', 'warning', true);
    }
});

promptContinueBtn.addEventListener('click', async () => {
    promptModal.style.display = 'none';
    await fetch('/state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: 'RUN' })
    });
});

// ════════════════════════════════════════════════════════════════
//  Process Button → Batch Name Prompt → Upload
// ════════════════════════════════════════════════════════════════

processBtn.addEventListener('click', () => {
    if (!selectedFile) return;
    showBatchNamePrompt();
});

async function startProcessing() {
    processBtn.disabled = true;
    processBtn.textContent = 'Starting...';
    consoleOutput.innerHTML = '';

    const formData = new FormData();
    formData.append('file', selectedFile);

    try {
        const url = `/upload?start_from=${startFromInput.value}&batch_name=${encodeURIComponent(batchName)}`;
        const response = await fetch(url, {
            method: 'POST',
            body: formData
        });
        const result = await response.json();

        if (result.status === 'success') {
            uploadSection.style.display = 'none';
            dashboardSection.style.display = 'block';
            jobFilename.textContent = batchName || selectedFile.name;

            pauseBtn.disabled = false;
            stopBtn.disabled = false;
            cancelBtn.disabled = false;

            setStatus('Initializing automation...', 'info', true);
            startLogStream();
        } else {
            alert(result.message);
            processBtn.disabled = false;
            processBtn.textContent = 'Start Processing';
        }
    } catch (err) {
        alert('Failed to start processing: ' + err);
        processBtn.disabled = false;
        processBtn.textContent = 'Start Processing';
    }
}

// ════════════════════════════════════════════════════════════════
//  Status & Progress
// ════════════════════════════════════════════════════════════════

function setStatus(message, type = 'info', pulsing = true) {
    currentStatusText.textContent = message;
    statusIndicator.className = 'status-indicator';
    if (pulsing) statusIndicator.classList.add('animate-pulse');

    if (type === 'success') statusIndicator.style.backgroundColor = 'var(--success)';
    else if (type === 'error') statusIndicator.style.backgroundColor = 'var(--error)';
    else if (type === 'warning') statusIndicator.style.backgroundColor = 'var(--warning)';
    else statusIndicator.style.backgroundColor = 'var(--info)';
}

function updateProgressUI() {
    statSuccess.textContent = successCount;
    statSkipped.textContent = skipCount;
    statErrors.textContent = errorCount;

    if (totalRecords > 0) {
        const processed = successCount + skipCount + errorCount;
        let percentage = Math.min(100, Math.round((processed / totalRecords) * 100));
        progressBarFill.style.width = percentage + '%';
        progressText.textContent = percentage + '% Complete (' + processed + '/' + totalRecords + ')';
    }
}

// ════════════════════════════════════════════════════════════════
//  Log Parsing — Extract stats from pipeline output
// ════════════════════════════════════════════════════════════════

function parseLogLine(line) {
    // Total records loaded
    if (line.includes('Loaded') && line.includes('valid IRN records')) {
        const match = line.match(/Loaded (\d+) valid/);
        if (match) {
            totalRecords = parseInt(match[1]);
            statTotal.textContent = totalRecords;
            updateProgressUI();
        }
    }

    // Modifier worker completed a PDF
    if (line.includes('Completed:') && line.includes('ModifierWorker')) {
        successCount++;
        updateProgressUI();
        const filename = line.split('Completed:')[1].trim();
        setStatus('Processed: ' + filename, 'success', true);
    }

    // Legacy format (Worker modification OK)
    if (line.includes('Worker modification OK:')) {
        successCount++;
        updateProgressUI();
        const filename = line.split('OK:')[1].trim();
        setStatus('Processed: ' + filename, 'success', true);
    }

    // Skipped (already processed)
    if (line.includes('[SKIPPED] Already processed:')) {
        skipCount++;
        updateProgressUI();
        const filename = line.split('processed:')[1].trim();
        setStatus('Skipped: ' + filename, 'warning', true);
    }

    // Jump (resume from row N)
    if (line.includes('[UI_JUMP]')) {
        const jumpCount = parseInt(line.split('[UI_JUMP]')[1].trim());
        if (!isNaN(jumpCount)) {
            skipCount += jumpCount;
            updateProgressUI();
            setStatus('Skipped ' + jumpCount + ' previous rows', 'info', true);
        }
    }

    // Errors
    if (line.includes('permanently failed') || line.includes('Worker failed') || line.includes('Fatal error')) {
        errorCount++;
        updateProgressUI();
        setStatus('Error processing a file', 'error', true);
    }

    // Download progress
    if (line.includes('Invoice:')) {
        const match = line.match(/Invoice:\s*([^\s|]+)/);
        if (match) setStatus('Downloading: ' + match[1], 'info', true);
    }

    // Pipeline status messages
    if (line.includes('Preprocessing raw Excel file')) setStatus('Preprocessing Excel file...', 'info', true);
    if (line.includes('Launching Chromium')) setStatus('Launching browser...', 'info', true);
    if (line.includes('Navigating to')) setStatus('Opening GST portal...', 'info', true);
    if (line.includes('Pipeline] Starting')) setStatus('Starting processing pipeline...', 'info', true);
    if (line.includes('Pipeline] Shutdown complete')) setStatus('Pipeline finished', 'success', false);

    // Login prompt
    if (line.includes('[UI_PROMPT]')) {
        const msg = line.split('[UI_PROMPT]')[1].trim();
        promptText.textContent = msg;
        promptModal.style.display = 'flex';
        setStatus('Waiting for manual login...', 'warning', true);
    }

    if (line.includes('User confirmed login') || line.includes('User confirmed re-login')) {
        setStatus('Logged in — downloading...', 'success', true);
    }

    if (line.includes('Automation paused')) setStatus('Paused', 'warning', false);
    if (line.includes('Automation resumed')) setStatus('Resuming...', 'info', true);
}

// ════════════════════════════════════════════════════════════════
//  SSE Log Stream
// ════════════════════════════════════════════════════════════════

function startLogStream() {
    if (eventSource) eventSource.close();

    eventSource = new EventSource('/progress');

    eventSource.onmessage = function(event) {
        const line = event.data;
        if (line.trim() !== '') {
            appendLog(line);
            parseLogLine(line);
        }

        // Check for terminal events
        if (line.includes('Automation completed successfully') ||
            line.includes('Automation finished with error') ||
            line.includes('Automation stopped by user') ||
            line.includes('Automation cancelled by user')) {

            eventSource.close();
            pauseBtn.disabled = true;
            stopBtn.disabled = true;
            cancelBtn.disabled = true;

            if (line.includes('completed successfully')) {
                setStatus('Completed Successfully!', 'success', false);
                progressBarFill.style.width = '100%';
                if (totalRecords === 0) progressText.textContent = '100% Complete';
            } else if (line.includes('stopped by user')) {
                setStatus('Stopped — Progress saved', 'success', false);
            } else if (line.includes('cancelled')) {
                setStatus('Cancelled — Files cleaned up', 'warning', false);
                setTimeout(() => resetUI(), 2500);
            } else {
                setStatus('Finished with errors', 'error', false);
            }

            processBtn.disabled = false;
            processBtn.textContent = 'Start Processing';
            resetBtn.style.display = 'block';
        }
    };

    eventSource.onerror = function(err) {
        console.error("EventSource error:", err);
    };
}

// ════════════════════════════════════════════════════════════════
//  Console Log Display
// ════════════════════════════════════════════════════════════════

function appendLog(message, type = 'info') {
    const line = document.createElement('div');
    line.className = 'log-line';

    if (message.includes('[SYSTEM]')) line.classList.add('log-system');
    else if (message.includes('ERROR') || type === 'error') line.classList.add('log-error');
    else if (message.includes('WARNING') || message.includes('UI_PROMPT')) line.classList.add('log-warn');
    else line.classList.add('log-info');

    line.textContent = message;
    consoleOutput.appendChild(line);

    // Keep only the last 80 log lines
    while (consoleOutput.children.length > 80) {
        consoleOutput.removeChild(consoleOutput.firstChild);
    }
    consoleOutput.scrollTop = consoleOutput.scrollHeight;
}
