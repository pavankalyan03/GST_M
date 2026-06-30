document.addEventListener('DOMContentLoaded', () => {

    // UI Elements
    const settingsToggle = document.getElementById('settings-toggle');
    const settingsPanel = document.getElementById('settings-panel');
    const configForm = document.getElementById('config-form');

    const dropzone = document.getElementById('dropzone');
    const fileInput = document.getElementById('file-input');
    const btnPreprocess = document.getElementById('btn-preprocess');
    const batchNameInput = document.getElementById('batch-name');

    const areaUpload = document.getElementById('upload-area');
    const areaCheckpoint = document.getElementById('checkpoint-area');
    const areaRunning = document.getElementById('running-area');

    // Checkpoint Elements
    const cpValidCount = document.getElementById('cp-valid-count');
    const cpBatchName = document.getElementById('cp-batch-name');
    const btnDownloadExcel = document.getElementById('btn-download-excel');
    const btnStartAuto = document.getElementById('btn-start-automation');
    const btnCancelCp = document.getElementById('btn-cancel-checkpoint');

    // Running Controls
    const btnPause = document.getElementById('btn-pause');
    const btnResume = document.getElementById('btn-resume');
    const btnStop = document.getElementById('btn-stop');
    const btnPromptContinue = document.getElementById('btn-prompt-continue');
    const btnNewJob = document.getElementById('btn-new-job');
    const btnRetryFailed = document.getElementById('btn-retry-failed');
    const runningPulse = document.getElementById('running-pulse');

    // Metrics
    const metricTotal = document.getElementById('metric-total');
    const metricProcessed = document.getElementById('metric-processed');
    const metricErrors = document.getElementById('metric-errors');
    const metricSpeed = document.getElementById('metric-speed');

    const progressBar = document.getElementById('main-progress-bar');
    const progressText = document.getElementById('main-progress-text');

    // Tabs
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');
    const errorTableBody = document.getElementById('error-table-body');
    const tabErrorCount = document.getElementById('tab-error-count');

    let selectedFile = null;
    let currentCleanedExcelPath = null;
    let currentBatchName = null;

    // ── 1. Settings Accordion ──
    settingsToggle.addEventListener('click', () => {
        settingsPanel.style.display = settingsPanel.style.display === 'none' ? 'block' : 'none';
    });

    fetch('/config').then(r => r.json()).then(data => {
        if (data.status === 'success') {
            document.getElementById('cfg-gstin').value = data.config.gstin || '';
            document.getElementById('cfg-header_name').value = data.config.header_name || '';
            document.getElementById('cfg-recipient_name').value = data.config.recipient_name || '';
            document.getElementById('cfg-recipient_address').value = data.config.recipient_address || '';
            document.getElementById('cfg-ship_to_name').value = data.config.ship_to_name || '';
            document.getElementById('cfg-ship_to_address').value = data.config.ship_to_address || '';
            document.getElementById('cfg-original_folder').value = data.config.original_folder || '';
            document.getElementById('cfg-processed_folder').value = data.config.processed_folder || '';
            document.getElementById('cfg-processed_excel_folder').value = data.config.processed_excel_folder || '';
        }
    });

    configForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const payload = {
            gstin: document.getElementById('cfg-gstin').value,
            header_name: document.getElementById('cfg-header_name').value,
            recipient_name: document.getElementById('cfg-recipient_name').value,
            recipient_address: document.getElementById('cfg-recipient_address').value,
            ship_to_name: document.getElementById('cfg-ship_to_name').value,
            ship_to_address: document.getElementById('cfg-ship_to_address').value,
            original_folder: document.getElementById('cfg-original_folder').value,
            processed_folder: document.getElementById('cfg-processed_folder').value,
            processed_excel_folder: document.getElementById('cfg-processed_excel_folder').value
        };
        fetch('/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).then(r => r.json()).then(data => {
            if (data.status === 'success') {
                alert('Settings saved!');
                settingsPanel.style.display = 'none';
            }
        });
    });

    // ── 2. Drag & Drop Upload ──
    dropzone.addEventListener('click', () => fileInput.click());

    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropzone.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
        dropzone.addEventListener(eventName, () => dropzone.style.borderColor = 'var(--accent)', false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropzone.addEventListener(eventName, () => dropzone.style.borderColor = 'rgba(99, 102, 241, 0.5)', false);
    });

    dropzone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const files = dt.files;
        handleFiles(files);
    });

    fileInput.addEventListener('change', function () {
        handleFiles(this.files);
    });

    function handleFiles(files) {
        if (files.length > 0) {
            selectedFile = files[0];
            dropzone.querySelector('h3').innerText = selectedFile.name;
        }
    }

    btnPreprocess.addEventListener('click', async () => {
        if (!selectedFile) {
            alert('Please select an Excel or ZIP file first.');
            return;
        }

        btnPreprocess.disabled = true;
        btnPreprocess.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Processing...';

        const formData = new FormData();
        formData.append('file', selectedFile);
        formData.append('batch_name', batchNameInput.value);

        try {
            const response = await fetch('/upload_and_preprocess', {
                method: 'POST',
                body: formData
            });
            const data = await response.json();

            if (data.status === 'success') {
                currentCleanedExcelPath = data.cleaned_excel;
                currentBatchName = data.batch_name;

                cpValidCount.innerText = data.valid_count;
                metricTotal.innerText = data.valid_count;
                cpBatchName.innerText = data.batch_name;

                const btnCpRetryFailed = document.getElementById('btn-cp-retry-failed');
                if (btnCpRetryFailed) {
                    btnCpRetryFailed.style.display = data.has_failed_irns ? 'inline-flex' : 'none';
                }

                btnDownloadExcel.href = '/download_excel?filepath=' + encodeURIComponent(data.cleaned_excel);

                areaUpload.style.display = 'none';
                areaCheckpoint.style.display = 'flex';
            } else {
                alert('Error: ' + data.message);
            }
        } catch (error) {
            alert('Upload failed: ' + error);
        } finally {
            btnPreprocess.disabled = false;
            btnPreprocess.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> Upload & Preprocess';
        }
    });

    // ── 3. Checkpoint Actions ──
    btnCancelCp.addEventListener('click', () => {
        fetch('/state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: 'CANCEL' }) });
        areaCheckpoint.style.display = 'none';
        areaUpload.style.display = 'flex';
        selectedFile = null;
        if (fileInput) fileInput.value = '';
        dropzone.querySelector('h3').innerText = 'Drag & Drop Excel or ZIP here';
    });

    btnStartAuto.addEventListener('click', async () => {
        areaCheckpoint.style.display = 'none';
        areaRunning.style.display = 'flex';

        await fetch('/start_automation', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                cleaned_excel_path: currentCleanedExcelPath,
                batch_name: currentBatchName,
                start_from: 1,
                modify_invoices: document.getElementById('toggle-modify').checked
            })
        });
    });

    const btnCpRetryFailed = document.getElementById('btn-cp-retry-failed');
    if (btnCpRetryFailed) {
        btnCpRetryFailed.addEventListener('click', async () => {
            areaCheckpoint.style.display = 'none';
            areaRunning.style.display = 'flex';

            await fetch('/start_automation', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    cleaned_excel_path: currentCleanedExcelPath,
                    batch_name: currentBatchName,
                    start_from: 1,
                    retry_only: true,
                    modify_invoices: document.getElementById('toggle-modify').checked
                })
            });
        });
    }

    // ── 4. Control Buttons ──
    btnPromptContinue.addEventListener('click', () => {
        fetch('/state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: 'PROMPT_CONTINUE' }) });
    });
    btnPause.addEventListener('click', () => {
        fetch('/state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: 'PAUSE' }) });
    });
    btnResume.addEventListener('click', () => {
        fetch('/state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: 'RESUME' }) });
    });
    btnStop.addEventListener('click', () => {
        if (confirm("Are you sure you want to completely stop the automation?")) {
            fetch('/state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: 'STOP' }) });

            // Immediately reset UI visuals
            areaRunning.style.display = 'none';
            areaUpload.style.display = 'flex';

            selectedFile = null;
            if (fileInput) fileInput.value = '';
            document.getElementById('dropzone').querySelector('h3').innerText = 'Drag & Drop Excel or ZIP here';
            document.getElementById('batch-name').value = '';

            metricTotal.innerText = '0';
            metricProcessed.innerText = '0';
            metricErrors.innerText = '0';
            metricSpeed.innerText = '0 / min';
            progressBar.style.width = '0%';
            progressText.innerText = '0% Complete';
            document.getElementById('terminal-output').innerHTML = '<div class="log-line system">System initialized. Awaiting upload...</div>';
            errorTableBody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">No errors recorded yet.</td></tr>';
            tabErrorCount.innerText = '0';

            // Wait a few seconds for backend force-kill to complete before resetting IPC state
            setTimeout(() => {
                fetch('/reset_job', { method: 'POST' });
            }, 3500);
        }
    });
    if (btnNewJob) {
        btnNewJob.addEventListener('click', async () => {
            await fetch('/reset_job', { method: 'POST' });

            areaRunning.style.display = 'none';
            areaUpload.style.display = 'flex';

            selectedFile = null;
            if (fileInput) fileInput.value = '';
            document.getElementById('dropzone').querySelector('h3').innerText = 'Drag & Drop Excel or ZIP here';
            document.getElementById('batch-name').value = '';

            metricTotal.innerText = '0';
            metricProcessed.innerText = '0';
            metricErrors.innerText = '0';
            metricSpeed.innerText = '0 / min';
            progressBar.style.width = '0%';
            progressText.innerText = '0% Complete';
            document.getElementById('terminal-output').innerHTML = '<div class="log-line system">System initialized. Awaiting upload...</div>';
            errorTableBody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">No errors recorded yet.</td></tr>';
            tabErrorCount.innerText = '0';
        });
    }

    if (btnRetryFailed) {
        btnRetryFailed.addEventListener('click', async () => {
            if (btnStop.style.display !== 'none' || btnResume.style.display !== 'none') {
                await fetch('/state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: 'STOP' }) });
                await new Promise(r => setTimeout(r, 2000));
            }

            if (currentBatchName && currentCleanedExcelPath) {
                metricTotal.innerText = '0';
                metricProcessed.innerText = '0';
                metricErrors.innerText = '0';
                metricSpeed.innerText = '0 / min';
                progressBar.style.width = '0%';
                progressText.innerText = '0% Complete';
                document.getElementById('terminal-output').innerHTML = '<div class="log-line system">Retrying failed IRNs...</div>';
                errorTableBody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">No errors recorded yet.</td></tr>';
                tabErrorCount.innerText = '0';

                await fetch('/start_automation', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        cleaned_excel_path: currentCleanedExcelPath,
                        batch_name: currentBatchName,
                        start_from: 1,
                        retry_only: true,
                        modify_invoices: document.getElementById('toggle-modify').checked
                    })
                });
            } else {
                alert("Cannot retry: No current batch found.");
            }
        });
    }

    // ── 5. Tabs Logic ──
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            tabBtns.forEach(b => b.classList.remove('active'));
            tabContents.forEach(c => c.style.display = 'none');

            btn.classList.add('active');
            document.getElementById(btn.dataset.tab).style.display = 'block';
        });
    });

    // ── 6. SSE Dashboard Stream ──
    const terminalOutput = document.getElementById('terminal-output');
    const evtSource = new EventSource("/api/dashboard_stream");

    evtSource.onopen = function () {
        const badge = document.getElementById('status-badge');
        if (badge) {
            badge.className = 'badge success';
            badge.innerHTML = '<i class="fa-solid fa-circle-check"></i> System Online';
        }
    };

    evtSource.onerror = function () {
        const badge = document.getElementById('status-badge');
        if (badge) {
            badge.className = 'badge danger';
            badge.innerHTML = '<i class="fa-solid fa-circle-xmark"></i> System Offline';
        }
    };

    let startTime = null;
    let previousProcessed = 0;

    evtSource.onmessage = function (event) {
        if (event.data === ": keep-alive") return;

        try {
            const data = JSON.parse(event.data);

            // Health
            if (data.health) {
                document.getElementById('cpu-val').innerText = data.health.cpu_percent + '%';
                document.getElementById('mem-val').innerText = data.health.mem_percent + '%';
            }

            // Logs
            if (data.logs && data.logs.length > 0) {
                data.logs.forEach(log => {
                    const el = document.createElement('div');
                    el.className = 'log-line';
                    if (log.includes('ERROR') || log.includes('Failed')) el.classList.add('error');
                    else if (log.includes('SUCCESS') || log.includes('successfully')) el.classList.add('success');
                    else if (log.includes('SYSTEM')) el.classList.add('system');

                    el.textContent = log;
                    terminalOutput.appendChild(el);

                    // Simple logic to set total if log contains it
                    if (log.includes('[UI_TOTAL]')) {
                        const total = parseInt(log.split(' ')[1]);
                        metricTotal.innerText = total;
                    }
                });
                
                // Cap terminal logs to prevent memory leak and browser crash
                while (terminalOutput.childElementCount > 1000) {
                    terminalOutput.removeChild(terminalOutput.firstChild);
                }
                
                terminalOutput.scrollTop = terminalOutput.scrollHeight;
            }

            // IPC Status (for UI layout toggles)
            if (data.ipc_status) {
                if (data.ipc_status === 'PENDING_CONFIRMATION') {
                    areaUpload.style.display = 'none';
                    areaRunning.style.display = 'none';
                    areaCheckpoint.style.display = 'flex';
                } else if (data.ipc_status === 'RUN') {
                    areaUpload.style.display = 'none';
                    areaCheckpoint.style.display = 'none';
                    areaRunning.style.display = 'flex';
                    btnPause.style.display = 'inline-flex';
                    btnResume.style.display = 'none';
                    btnPromptContinue.style.display = 'none';
                    if (btnRetryFailed) btnRetryFailed.style.display = 'none';
                } else if (data.ipc_status === 'PAUSE' || data.ipc_status === 'PAUSE_ERRORS') {
                    btnPause.style.display = 'none';
                    btnResume.style.display = 'inline-flex';
                    btnPromptContinue.style.display = 'none';
                    if (data.ipc_status === 'PAUSE_ERRORS' && btnRetryFailed) {
                        btnRetryFailed.style.display = 'inline-flex';
                    }
                } else if (data.ipc_status === 'UI_PROMPT') {
                    areaUpload.style.display = 'none';
                    areaCheckpoint.style.display = 'none';
                    areaRunning.style.display = 'flex';
                    btnPause.style.display = 'none';
                    btnResume.style.display = 'none';
                    btnPromptContinue.style.display = 'inline-flex';
                }
            }

            // Pipeline State
            if (data.pipeline) {
                const p = data.pipeline;

                // Metrics
                const metrics = p.metrics || {};
                metricProcessed.innerText = metrics.processed || 0;
                metricErrors.innerText = metrics.errors || 0;
                if (metrics.total) {
                    metricTotal.innerText = metrics.total;
                }

                const t = metrics.total || parseInt(metricTotal.innerText) || 0;
                if (t > 0) {
                    const pct = Math.min(100, Math.round(((metrics.processed || 0) + (metrics.errors || 0)) / t * 100));
                    progressBar.style.width = pct + '%';
                    progressText.innerText = pct + '% Complete';
                }

                // Throughput calc
                if (p.status !== 'COMPLETED' && p.status !== 'CANCELLED') {
                    if ((metrics.processed || 0) > previousProcessed) {
                        previousProcessed = metrics.processed;
                        if (!startTime) startTime = Date.now();
                    }
                    if (startTime && metrics.processed > 0) {
                        const elapsedMs = Date.now() - startTime;
                        // Only calculate speed if at least 3 seconds have passed to avoid division by near-zero
                        if (elapsedMs > 3000) {
                            const elapsedMin = elapsedMs / 60000;
                            const speed = Math.round(metrics.processed / elapsedMin);
                            metricSpeed.innerText = speed + ' / min';
                        } else {
                            metricSpeed.innerText = 'Calculating...';
                        }
                    }
                }

                // Worker Stages
                const wDownloader = document.getElementById('worker-downloader');
                const wModifier = document.getElementById('worker-modifier');

                const dWorker = (p.workers && p.workers.downloader) || {};
                const mWorker = (p.workers && p.workers.modifier) || {};

                if (dWorker.status === 'processing') {
                    wDownloader.className = 'worker-stage active';
                    wDownloader.querySelector('.status-text').innerText = 'Downloading: ' + (dWorker.current || '...');
                } else if (dWorker.status === 'error') {
                    wDownloader.className = 'worker-stage error';
                } else {
                    wDownloader.className = 'worker-stage';
                    wDownloader.querySelector('.status-text').innerText = dWorker.status || 'Idle';
                }

                if (mWorker.status === 'processing') {
                    wModifier.className = 'worker-stage active';
                    wModifier.querySelector('.status-text').innerText = 'Modifying: ' + (mWorker.current || '...');
                } else if (mWorker.status === 'error') {
                    wModifier.className = 'worker-stage error';
                } else {
                    wModifier.className = 'worker-stage';
                    wModifier.querySelector('.status-text').innerText = mWorker.status || 'Idle';
                }

                // Queue List
                const qList = document.getElementById('queue-list');
                if (p.queue && p.queue.length > 0) {
                    qList.innerHTML = '';
                    p.queue.slice(0, 5).forEach(irn => {
                        const li = document.createElement('li');
                        li.innerText = irn.substring(0, 25) + '...';
                        qList.appendChild(li);
                    });
                } else {
                    qList.innerHTML = '<li class="empty-msg">Queue is empty</li>';
                }

                // Errors
                if (p.errors) {
                    if (tabErrorCount.innerText != p.errors.length) {
                        tabErrorCount.innerText = p.errors.length;
                        errorTableBody.innerHTML = '';
                        // Cap at 100 to prevent browser crash from massive DOM reflow
                        const displayErrors = p.errors.slice(-100); 
                        displayErrors.forEach((e, idx) => {
                            const tr = document.createElement('tr');
                            const identifier = e.irn || e.file || "-";
                            // Calculate true index based on sliced offset
                            const trueIdx = p.errors.length > 100 ? p.errors.length - 100 + idx : idx;
                            tr.innerHTML = `<td>${trueIdx + 1}</td><td>${e.type}</td><td>${identifier.substring(0, 15)}...</td><td class="text-danger">${e.message}</td>`;
                            errorTableBody.appendChild(tr);
                        });
                    }
                }

                // Control Center Status
                if (p.status) {
                    let title = "Automation Running";
                    let pulseColor = "var(--success)";
                    let pulseAnim = "pulse 2s infinite";
                    let isDone = false;

                    if (p.status === 'COMPLETED') {
                        title = "Automation Completed";
                        pulseAnim = "none";
                        pulseColor = "var(--accent)";
                        isDone = true;
                    } else if (p.status === 'CANCELLED') {
                        title = "Automation Cancelled";
                        pulseAnim = "none";
                        pulseColor = "var(--error)";
                        isDone = true;
                    } else if (p.status === 'PAUSED') {
                        title = "Automation Paused";
                        pulseAnim = "none";
                        pulseColor = "var(--warning)";
                    } else if (p.status === 'PAUSED_ERRORS') {
                        title = "Paused (Too Many Errors)";
                        pulseAnim = "none";
                        pulseColor = "var(--error)";
                    } else if (p.status === 'WAITING_FOR_LOGIN') {
                        title = "Waiting for Manual Login...";
                        pulseAnim = "none";
                        pulseColor = "var(--warning)";
                    } else if (p.status === 'INITIALIZING') {
                        title = "Initializing Pipeline...";
                        pulseAnim = "pulse 2s infinite";
                        pulseColor = "var(--success)";
                    }

                    document.getElementById('current-state-title').innerText = title;
                    runningPulse.style.animation = pulseAnim;
                    runningPulse.style.background = pulseColor;

                    if (isDone) {
                        btnPause.style.display = 'none';
                        btnResume.style.display = 'none';
                        btnStop.style.display = 'none';
                        btnPromptContinue.style.display = 'none';
                        if (btnNewJob) btnNewJob.style.display = 'inline-flex';
                        if (btnRetryFailed && p.errors && p.errors.length > 0) {
                            btnRetryFailed.style.display = 'inline-flex';
                        }
                    } else {
                        if (btnNewJob) btnNewJob.style.display = 'none';
                        btnStop.style.display = 'inline-flex';
                    }
                }
            }

        } catch (e) {
            console.error("Error parsing SSE data", e);
        }
    };

    // Note: Error fetching polling was removed since errors are streamed via SSE.
});
