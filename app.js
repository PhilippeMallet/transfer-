// app.js

// Predefined financial metric definitions
const FINANCIAL_FIELDS = [
    { key: 'pe_12m_fw', label: 'PE 12m Forward', unit: 'x', type: 'number' },
    { key: 'eps_2025', label: 'EPS 2025', unit: '', type: 'number' },
    { key: 'eps_2026', label: 'EPS 2026', unit: '', type: 'number' },
    { key: 'eps_2027', label: 'EPS 2027', unit: '', type: 'number' },
    { key: 'ebitda_margin', label: 'EBITDA Margin', unit: '%', type: 'number' },
    { key: 'net_debt_ebitda', label: 'Net Debt / EBITDA', unit: 'x', type: 'number' },
    { key: 'roce_wacc', label: 'ROCE − WACC', unit: '%', type: 'number' },
];

function defaultMetrics() {
    const m = {};
    FINANCIAL_FIELDS.forEach(f => m[f.key] = '');
    return m;
}

/**
 * Core Application State — persisted to localStorage.
 */
const STORAGE_KEY = 'companyMapState';

class StateManager {
    constructor() {
        const saved = this._load();
        if (saved) {
            this.companies = saved.companies || [];
            this.groups = saved.groups || [];
            this.arrows = saved.arrows || [];
            this.transform = saved.transform || { x: 0, y: 0, scale: 1 };
            this.companies.forEach(c => { if (!c.metrics) c.metrics = defaultMetrics(); });
        } else {
            this.companies = [
                {
                    id: 'comp-1', name: 'DeepMind', x: 300, y: 200, metrics: defaultMetrics(),
                    quantitative: [{ id: 'q1-1', name: 'Employees', value: '1,000+' }, { id: 'q1-2', name: 'Founded', value: '2010' }],
                    qualitative: [{ id: 'l1-1', name: 'Focus', value: 'Artificial General Intelligence' }, { id: 'l1-2', name: 'Acquisition', value: 'Google in 2014' }]
                },
                {
                    id: 'comp-2', name: 'OpenAI', x: 600, y: 350, metrics: defaultMetrics(),
                    quantitative: [{ id: 'q2-1', name: 'Valuation', value: '$80B+' }, { id: 'q2-2', name: 'Founded', value: '2015' }],
                    qualitative: [{ id: 'l2-1', name: 'Product', value: 'ChatGPT, DALL-E' }]
                },
                {
                    id: 'comp-3', name: 'Anthropic', x: 400, y: 500, metrics: defaultMetrics(),
                    quantitative: [{ id: 'q3-1', name: 'Funding', value: '$7B+' }],
                    qualitative: [{ id: 'l3-1', name: 'Focus', value: 'Constitutional AI' }]
                }
            ];
            this.groups = [];
            this.arrows = [];
            this.transform = { x: 0, y: 0, scale: 1 };
            this.save();
        }
        this.selectedCompanyId = null;
    }

    save() {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify({
                companies: this.companies, groups: this.groups, arrows: this.arrows, transform: this.transform
            }));
        } catch (e) { console.warn('Save failed:', e); }
    }

    _load() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (e) { return null; }
    }

    getCompany(id) {
        return this.companies.find(c => c.id === id);
    }

    updateCompanyPosition(id, x, y) {
        const comp = this.getCompany(id);
        if (comp) {
            comp.x = x;
            comp.y = y;
        }
    }

    addFeature(companyId, type, featureName, featureValue) {
        const comp = this.getCompany(companyId);
        if (!comp) return;

        const feature = {
            id: `feat-${Date.now()}`,
            name: featureName,
            value: featureValue
        };

        comp[type].push(feature);
        this.save();
        return feature;
    }

    updateFeature(companyId, type, featureId, newName, newValue) {
        const comp = this.getCompany(companyId);
        if (!comp) return;
        const feat = comp[type].find(f => f.id === featureId);
        if (feat) {
            feat.name = newName;
            feat.value = newValue;
            this.save();
        }
    }

    removeFeature(companyId, type, featureId) {
        const comp = this.getCompany(companyId);
        if (!comp) return;

        comp[type] = comp[type].filter(f => f.id !== featureId);
        this.save();
    }

    addCompany(name) {
        const company = {
            id: `comp-${Date.now()}`,
            name: name,
            x: 300 + Math.random() * 300,
            y: 200 + Math.random() * 300,
            metrics: defaultMetrics(),
            quantitative: [],
            qualitative: []
        };
        this.companies.push(company);
        this.save();
        return company;
    }

    updateMetric(companyId, key, value) {
        const comp = this.getCompany(companyId);
        if (comp) comp.metrics[key] = value;
        this.save();
    }

    removeCompany(id) {
        this.companies = this.companies.filter(c => c.id !== id);
        // Cascade delete arrows that reference this company
        this.arrows = this.arrows.filter(a => a.fromId !== id && a.toId !== id);
        if (this.selectedCompanyId === id) this.selectedCompanyId = null;
        this.save();
    }

    // --- Arrow methods ---
    addArrow(fromId, toId, label) {
        const arrow = {
            id: `arrow-${Date.now()}`,
            fromId,
            toId,
            label: label || ''
        };
        this.arrows.push(arrow);
        this.save();
        return arrow;
    }

    removeArrow(id) {
        this.arrows = this.arrows.filter(a => a.id !== id);
        this.save();
    }

    addGroup(name, shape, color) {
        const group = {
            id: `grp-${Date.now()}`,
            name: name,
            shape: shape, // 'rect' or 'circle'
            color: color,
            x: 250 + Math.random() * 200,
            y: 150 + Math.random() * 200,
            width: 300,
            height: 250
        };
        this.groups.push(group);
        this.save();
        return group;
    }

    removeGroup(id) {
        this.groups = this.groups.filter(g => g.id !== id);
        this.save();
    }

    getGroup(id) {
        return this.groups.find(g => g.id === id);
    }

    updateGroupPosition(id, x, y) {
        const g = this.getGroup(id);
        if (g) { g.x = x; g.y = y; }
    }

    updateGroupSize(id, w, h) {
        const g = this.getGroup(id);
        if (g) { g.width = Math.max(100, w); g.height = Math.max(80, h); }
    }
}

/**
 * UI Controller
 * Handles DOM manipulation and events
 */
class UIController {
    constructor(state) {
        this.state = state;

        // DOM Elements
        this.nodesLayer = document.getElementById('nodes-layer');
        this.shapesLayer = document.getElementById('shapes-layer');
        this.arrowsLayer = document.getElementById('arrows-layer');
        this.mapContainer = document.getElementById('map-container');
        this.mapBackground = document.getElementById('map-background');

        this.detailsPanel = document.getElementById('details-panel');
        this.closePanelBtn = document.getElementById('close-panel');
        this.deleteCompanyBtn = document.getElementById('delete-company');
        this.companyNameHeading = document.getElementById('company-name');

        this.quantList = document.querySelector('#quantitative-data .data-list');
        this.qualList = document.querySelector('#qualitative-data .data-list');

        this.featureModal = document.getElementById('feature-modal');
        this.featureForm = document.getElementById('feature-form');
        this.cancelFeatureBtn = document.getElementById('cancel-feature');

        this.companyModal = document.getElementById('company-modal');
        this.companyForm = document.getElementById('company-form');
        this.cancelCompanyBtn = document.getElementById('cancel-company');
        this.addCompanyFab = document.getElementById('add-company-fab');

        // Group state
        this.pendingGroupShape = null;
        this.groupModal = document.getElementById('group-modal');
        this.groupForm = document.getElementById('group-form');
        this.cancelGroupBtn = document.getElementById('cancel-group');
        this.selectedGroupColor = '#3b82f6';
        this.isDraggingGroup = false;
        this.isResizingGroup = false;
        this.activeGroupId = null;
        this.groupDragStart = { x: 0, y: 0 };
        this.groupInitialPos = { x: 0, y: 0 };
        this.groupInitialSize = { w: 0, h: 0 };

        // Arrow state
        this.isArrowMode = false;
        this.arrowSourceId = null; // first company clicked
        this.arrowTargetId = null; // second company clicked
        this.addArrowBtn = document.getElementById('add-arrow-btn');
        this.arrowModal = document.getElementById('arrow-modal');
        this.arrowForm = document.getElementById('arrow-form');
        this.cancelArrowBtn = document.getElementById('cancel-arrow');
        this.statusBar = null; // floating hint bar

        // Dragging state
        this.isDraggingMap = false;
        this.isDraggingNode = false;
        this.draggedNodeId = null;
        this.dragStart = { x: 0, y: 0 };
        this.initialTransform = { x: 0, y: 0 };
        this.initialNodePos = { x: 0, y: 0 };

        // Adding Feature State
        this.addingFeatureType = null; // 'quantitative' or 'qualitative'
        this.editingFeatureId = null;  // null = adding new, string = editing existing

        this.initEventListeners();
        this.renderMap();
    }

    initEventListeners() {
        // --- Map Panning ---
        this.mapContainer.addEventListener('mousedown', (e) => {
            // Ignore if clicking on a node or group shape
            if (e.target.closest('.company-node') || e.target.closest('.group-shape')) return;

            this.isDraggingMap = true;
            this.dragStart = { x: e.clientX, y: e.clientY };
            this.initialTransform = { ...this.state.transform };
            this.deselectAll();
        });

        // Global mouse move for dragging Map or Nodes
        window.addEventListener('mousemove', (e) => {
            if (this.isDraggingMap) {
                const dx = e.clientX - this.dragStart.x;
                const dy = e.clientY - this.dragStart.y;

                this.state.transform.x = this.initialTransform.x + dx;
                this.state.transform.y = this.initialTransform.y + dy;

                this.applyMapTransform();
            } else if (this.isDraggingNode && this.draggedNodeId) {
                // Adjust for map scale if we had zooming
                const dx = (e.clientX - this.dragStart.x) / this.state.transform.scale;
                const dy = (e.clientY - this.dragStart.y) / this.state.transform.scale;

                const newX = this.initialNodePos.x + dx;
                const newY = this.initialNodePos.y + dy;

                this.state.updateCompanyPosition(this.draggedNodeId, newX, newY);
                this.updateNodeElPosition(this.draggedNodeId, newX, newY);
                this.renderArrows(); // keep arrows attached while dragging
            }
        });

        window.addEventListener('mouseup', () => {
            if (this.isDraggingNode) {
                this.isDraggingNode = false;
                const nodeEl = document.getElementById(`node-${this.draggedNodeId}`);
                if (nodeEl) nodeEl.style.zIndex = '';
                this.draggedNodeId = null;
                this.state.save();
            }
            if (this.isDraggingMap) {
                this.isDraggingMap = false;
                this.state.save();
            }
        });

        // --- Details Panel ---
        this.closePanelBtn.addEventListener('click', () => {
            this.deselectAll();
        });

        // --- Feature Buttons ---
        document.querySelectorAll('.add-feature-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.addingFeatureType = e.target.dataset.type;
                this.openFeatureModal();
            });
        });

        this.cancelFeatureBtn.addEventListener('click', () => {
            this.closeFeatureModal();
        });

        this.featureForm.addEventListener('submit', (e) => {
            e.submitter?.blur(); // prevent form issues
            e.preventDefault();
            this.handleFeatureSubmit();
        });

        // Event delegation for delete and edit feature buttons
        document.querySelector('.panel-content').addEventListener('click', (e) => {
            // Delete
            const deleteBtn = e.target.closest('.delete-feature-btn');
            if (deleteBtn && this.state.selectedCompanyId) {
                const featureId = deleteBtn.dataset.id;
                const type = deleteBtn.dataset.type;
                this.state.removeFeature(this.state.selectedCompanyId, type, featureId);
                this.renderDetailsPanel(this.state.selectedCompanyId);
                return;
            }

            // Edit (click on data content area)
            const editTarget = e.target.closest('.data-content-clickable');
            if (editTarget && this.state.selectedCompanyId) {
                const featureId = editTarget.dataset.id;
                const type = editTarget.dataset.type;
                this.addingFeatureType = type;
                this.openFeatureModal(featureId, type);
            }
        });

        // --- Add Company FAB ---
        this.addCompanyFab.addEventListener('click', (e) => {
            e.stopPropagation();
            this.openCompanyModal();
        });

        this.cancelCompanyBtn.addEventListener('click', () => {
            this.closeCompanyModal();
        });

        this.companyForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleCompanySubmit();
        });

        // --- Delete Company ---
        this.deleteCompanyBtn.addEventListener('click', () => {
            if (this.state.selectedCompanyId) {
                const id = this.state.selectedCompanyId;
                this.state.removeCompany(id);
                this.deselectAll();
                this.renderMap();
                this.renderArrows(); // remove arrows referencing deleted company
            }
        });

        // --- Arrow Mode ---
        this.addArrowBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleArrowMode();
        });

        this.cancelArrowBtn.addEventListener('click', () => this.closeArrowModal());
        this.arrowForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleArrowSubmit();
        });

        // Escape to cancel arrow mode
        window.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.isArrowMode) {
                this.exitArrowMode();
            }
        });
    }

    applyMapTransform() {
        const { x, y, scale } = this.state.transform;
        const t = `translate(${x}px, ${y}px) scale(${scale})`;
        this.nodesLayer.style.transform = t;
        this.shapesLayer.style.transform = t;
        this.arrowsLayer.style.transform = t;
        this.mapBackground.style.backgroundPosition = `${x}px ${y}px`;
    }

    // --- Node Rendering ---

    renderMap() {
        this.nodesLayer.innerHTML = '';
        this.state.companies.forEach(company => {
            const node = document.createElement('div');
            node.className = 'company-node';
            node.id = `node-${company.id}`;
            node.textContent = company.name;

            this.updateNodeElPositionInline(node, company.x, company.y);

            // Node Events
            node.addEventListener('mousedown', (e) => {
                e.stopPropagation(); // prevent map pan

                // If in arrow mode, handle arrow click instead of drag
                if (this.isArrowMode) {
                    this.handleArrowNodeClick(company.id);
                    return;
                }

                this.isDraggingNode = true;
                this.draggedNodeId = company.id;
                this.dragStart = { x: e.clientX, y: e.clientY };
                this.initialNodePos = { x: company.x, y: company.y };

                node.style.zIndex = 1000; // bring to front while dragging

                // Select node
                this.selectCompany(company.id);
            });

            this.nodesLayer.appendChild(node);
        });

        this.applyMapTransform();
    }

    updateNodeElPositionInline(el, x, y) {
        // We use left/top instead of transform translate to avoid clash with the CSS hover translate
        el.style.left = `${x}px`;
        el.style.top = `${y}px`;
    }

    updateNodeElPosition(id, x, y) {
        const el = document.getElementById(`node-${id}`);
        if (el) this.updateNodeElPositionInline(el, x, y);
    }

    // --- Selection and Panel ---

    selectCompany(id) {
        // Deselect previous
        document.querySelectorAll('.company-node').forEach(el => el.classList.remove('selected'));

        this.state.selectedCompanyId = id;

        // Add selected class
        const nodeEl = document.getElementById(`node-${id}`);
        if (nodeEl) nodeEl.classList.add('selected');

        this.renderDetailsPanel(id);
    }

    deselectAll() {
        document.querySelectorAll('.company-node').forEach(el => el.classList.remove('selected'));
        this.state.selectedCompanyId = null;
        this.detailsPanel.classList.add('hidden');
    }

    renderDetailsPanel(id) {
        const company = this.state.getCompany(id);
        if (!company) return;

        this.companyNameHeading.textContent = company.name;

        // Render predefined financial metrics
        this.renderMetricsGrid(company);

        this.renderFeatureList(this.quantList, company.quantitative, 'quantitative');
        this.renderFeatureList(this.qualList, company.qualitative, 'qualitative');

        this.detailsPanel.classList.remove('hidden');
    }

    renderMetricsGrid(company) {
        const grid = document.getElementById('metrics-grid');
        grid.innerHTML = '';

        FINANCIAL_FIELDS.forEach(field => {
            const row = document.createElement('div');
            row.className = 'metric-row';

            const currentValue = company.metrics[field.key] || '';

            row.innerHTML = `
                <span class="metric-label">${field.label}</span>
                <div class="metric-input-wrap">
                    <input class="metric-input"
                           type="text"
                           inputmode="decimal"
                           placeholder="—"
                           data-key="${field.key}"
                           value="${currentValue}">
                    <span class="metric-unit">${field.unit}</span>
                </div>
            `;

            // Save on blur or Enter
            const input = row.querySelector('.metric-input');
            input.addEventListener('change', () => {
                this.state.updateMetric(company.id, field.key, input.value.trim());
            });
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') { input.blur(); }
            });

            grid.appendChild(row);
        });
    }

    renderFeatureList(container, features, type) {
        container.innerHTML = '';
        if (features.length === 0) {
            container.innerHTML = `<div class="data-item-name" style="text-align:center; padding: 10px 0; opacity: 0.5;">No data added yet</div>`;
            return;
        }

        features.forEach(feat => {
            const html = `
                <div class="data-item" data-id="${feat.id}" data-type="${type}">
                    <div class="data-content data-content-clickable" data-id="${feat.id}" data-type="${type}" title="Click to edit">
                        <div class="data-item-name">${feat.name}</div>
                        <div class="data-item-value">${feat.value}</div>
                    </div>
                    <button class="delete-feature-btn" data-id="${feat.id}" data-type="${type}" title="Remove feature">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M18 6L6 18M6 6l12 12"></path>
                        </svg>
                    </button>
                </div>
            `;
            container.insertAdjacentHTML('beforeend', html);
        });
    }

    // --- Modals ---
    openFeatureModal(editId, editType) {
        this.featureForm.reset();
        this.editingFeatureId = editId || null;

        const type = editType || this.addingFeatureType;
        const isQualitative = type === 'qualitative';
        const isEditing = !!this.editingFeatureId;

        const titleAction = isEditing ? 'Edit' : 'Add';
        const titleType = isQualitative ? 'Note' : 'Metric';
        document.getElementById('modal-title').textContent = `${titleAction} ${titleType}`;

        // Toggle input vs textarea
        const input = document.getElementById('feature-value');
        const textarea = document.getElementById('feature-value-text');
        if (isQualitative) {
            input.style.display = 'none';
            input.removeAttribute('required');
            textarea.style.display = '';
            textarea.setAttribute('required', '');
            textarea.value = '';
        } else {
            textarea.style.display = 'none';
            textarea.removeAttribute('required');
            input.style.display = '';
            input.setAttribute('required', '');
            input.value = '';
        }

        // Pre-fill if editing
        if (isEditing && this.state.selectedCompanyId) {
            const company = this.state.getCompany(this.state.selectedCompanyId);
            const feat = company[type].find(f => f.id === editId);
            if (feat) {
                document.getElementById('feature-name').value = feat.name;
                if (isQualitative) {
                    textarea.value = feat.value;
                } else {
                    input.value = feat.value;
                }
            }
        }

        this.featureModal.classList.remove('hidden');
        document.getElementById('feature-name').focus();
    }

    closeFeatureModal() {
        this.featureModal.classList.add('hidden');
        this.addingFeatureType = null;
        this.editingFeatureId = null;
    }

    handleFeatureSubmit() {
        const nameInput = document.getElementById('feature-name').value.trim();
        const isQualitative = this.addingFeatureType === 'qualitative';
        const valueInput = isQualitative
            ? document.getElementById('feature-value-text').value.trim()
            : document.getElementById('feature-value').value.trim();

        if (nameInput && valueInput && this.state.selectedCompanyId && this.addingFeatureType) {
            if (this.editingFeatureId) {
                // Update existing
                this.state.updateFeature(this.state.selectedCompanyId, this.addingFeatureType, this.editingFeatureId, nameInput, valueInput);
            } else {
                // Add new
                this.state.addFeature(this.state.selectedCompanyId, this.addingFeatureType, nameInput, valueInput);
            }
            this.renderDetailsPanel(this.state.selectedCompanyId);
            this.closeFeatureModal();
        }
    }

    // --- Company Modal ---
    openCompanyModal() {
        this.companyForm.reset();
        this.companyModal.classList.remove('hidden');
        document.getElementById('new-company-name').focus();
    }

    closeCompanyModal() {
        this.companyModal.classList.add('hidden');
    }

    handleCompanySubmit() {
        const nameInput = document.getElementById('new-company-name').value.trim();
        if (nameInput) {
            const company = this.state.addCompany(nameInput);
            this.addNodeToMap(company);
            this.closeCompanyModal();
        }
    }

    // Add a single node to the map (instead of re-rendering everything)
    addNodeToMap(company) {
        const node = document.createElement('div');
        node.className = 'company-node new';
        node.id = `node-${company.id}`;
        node.textContent = company.name;

        this.updateNodeElPositionInline(node, company.x, company.y);

        node.addEventListener('mousedown', (e) => {
            e.stopPropagation();

            if (this.isArrowMode) {
                this.handleArrowNodeClick(company.id);
                return;
            }

            this.isDraggingNode = true;
            this.draggedNodeId = company.id;
            this.dragStart = { x: e.clientX, y: e.clientY };
            this.initialNodePos = { x: company.x, y: company.y };
            node.style.zIndex = 1000;
            this.selectCompany(company.id);
        });

        this.nodesLayer.appendChild(node);

        // Remove entrance animation class after it plays
        setTimeout(() => node.classList.remove('new'), 300);
    }

    // --- Group Shapes ---
    applyShapesTransform() {
        const { x, y, scale } = this.state.transform;
        this.shapesLayer.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
    }

    renderGroupShape(group) {
        const el = document.createElement('div');
        el.className = `group-shape ${group.shape}`;
        el.id = `group-${group.id}`;
        el.style.left = `${group.x}px`;
        el.style.top = `${group.y}px`;
        el.style.width = `${group.width}px`;
        el.style.height = `${group.height}px`;
        el.style.borderColor = group.color + '80';
        el.style.background = group.color + '0F';

        el.innerHTML = `
            <span class="group-label" style="color:${group.color}">${group.name}</span>
            <button class="group-delete" data-id="${group.id}" title="Delete group">&times;</button>
            <div class="group-resize" style="color:${group.color}"></div>
        `;

        // Drag group
        el.addEventListener('mousedown', (e) => {
            if (e.target.closest('.group-delete') || e.target.closest('.group-resize')) return;
            e.stopPropagation();
            this.isDraggingGroup = true;
            this.activeGroupId = group.id;
            this.groupDragStart = { x: e.clientX, y: e.clientY };
            this.groupInitialPos = { x: group.x, y: group.y };
        });

        // Resize handle
        const resizeHandle = el.querySelector('.group-resize');
        resizeHandle.addEventListener('mousedown', (e) => {
            e.stopPropagation();
            this.isResizingGroup = true;
            this.activeGroupId = group.id;
            this.groupDragStart = { x: e.clientX, y: e.clientY };
            this.groupInitialSize = { w: group.width, h: group.height };
        });

        // Delete
        el.querySelector('.group-delete').addEventListener('click', (e) => {
            e.stopPropagation();
            this.state.removeGroup(group.id);
            el.remove();
        });

        this.shapesLayer.appendChild(el);
    }

    initGroupEventListeners() {
        // Toolbar buttons
        document.getElementById('add-rect-group').addEventListener('click', (e) => {
            e.stopPropagation();
            this.pendingGroupShape = 'rect';
            this.openGroupModal();
        });
        document.getElementById('add-circle-group').addEventListener('click', (e) => {
            e.stopPropagation();
            this.pendingGroupShape = 'circle';
            this.openGroupModal();
        });

        // Group modal
        this.cancelGroupBtn.addEventListener('click', () => this.closeGroupModal());
        this.groupForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleGroupSubmit();
        });

        // Color picker
        document.getElementById('color-picker').addEventListener('click', (e) => {
            const swatch = e.target.closest('.color-swatch');
            if (!swatch) return;
            document.querySelectorAll('.color-swatch').forEach(s => s.classList.remove('active'));
            swatch.classList.add('active');
            this.selectedGroupColor = swatch.dataset.color;
        });

        // Mouse move for group drag/resize
        window.addEventListener('mousemove', (e) => {
            if (this.isDraggingGroup && this.activeGroupId) {
                const dx = (e.clientX - this.groupDragStart.x) / this.state.transform.scale;
                const dy = (e.clientY - this.groupDragStart.y) / this.state.transform.scale;
                const newX = this.groupInitialPos.x + dx;
                const newY = this.groupInitialPos.y + dy;
                this.state.updateGroupPosition(this.activeGroupId, newX, newY);
                const el = document.getElementById(`group-${this.activeGroupId}`);
                if (el) { el.style.left = `${newX}px`; el.style.top = `${newY}px`; }
            } else if (this.isResizingGroup && this.activeGroupId) {
                const dx = (e.clientX - this.groupDragStart.x) / this.state.transform.scale;
                const dy = (e.clientY - this.groupDragStart.y) / this.state.transform.scale;
                const newW = this.groupInitialSize.w + dx;
                const newH = this.groupInitialSize.h + dy;
                this.state.updateGroupSize(this.activeGroupId, newW, newH);
                const g = this.state.getGroup(this.activeGroupId);
                const el = document.getElementById(`group-${this.activeGroupId}`);
                if (el && g) { el.style.width = `${g.width}px`; el.style.height = `${g.height}px`; }
            }
        });

        window.addEventListener('mouseup', () => {
            if (this.isDraggingGroup || this.isResizingGroup) {
                this.state.save();
            }
            this.isDraggingGroup = false;
            this.isResizingGroup = false;
            this.activeGroupId = null;
        });
    }

    openGroupModal() {
        this.groupForm.reset();
        document.querySelectorAll('.color-swatch').forEach(s => s.classList.remove('active'));
        document.querySelector('.color-swatch').classList.add('active');
        this.selectedGroupColor = '#3b82f6';
        const shapeLabel = this.pendingGroupShape === 'rect' ? 'Rectangle' : 'Circle';
        document.getElementById('group-modal-title').textContent = `Add ${shapeLabel} Group`;
        this.groupModal.classList.remove('hidden');
        document.getElementById('group-name').focus();
    }

    closeGroupModal() {
        this.groupModal.classList.add('hidden');
        this.pendingGroupShape = null;
    }

    handleGroupSubmit() {
        const name = document.getElementById('group-name').value.trim();
        if (name && this.pendingGroupShape) {
            const group = this.state.addGroup(name, this.pendingGroupShape, this.selectedGroupColor);
            this.renderGroupShape(group);
            this.closeGroupModal();
        }
    }

    // ===== Arrow Mode Methods =====

    toggleArrowMode() {
        if (this.isArrowMode) {
            this.exitArrowMode();
        } else {
            this.enterArrowMode();
        }
    }

    enterArrowMode() {
        this.isArrowMode = true;
        this.arrowSourceId = null;
        this.arrowTargetId = null;
        this.addArrowBtn.classList.add('active');
        this.mapContainer.classList.add('arrow-drawing');
        this.deselectAll();
        this.showStatusBar('Click on the source company');
    }

    exitArrowMode() {
        this.isArrowMode = false;
        this.arrowSourceId = null;
        this.arrowTargetId = null;
        this.addArrowBtn.classList.remove('active');
        this.mapContainer.classList.remove('arrow-drawing');
        document.querySelectorAll('.company-node.arrow-source').forEach(el => el.classList.remove('arrow-source'));
        this.removeStatusBar();
    }

    handleArrowNodeClick(companyId) {
        if (!this.arrowSourceId) {
            // First click — pick source
            this.arrowSourceId = companyId;
            const nodeEl = document.getElementById(`node-${companyId}`);
            if (nodeEl) nodeEl.classList.add('arrow-source');
            const comp = this.state.getCompany(companyId);
            this.showStatusBar(`Source: ${comp ? comp.name : '?'} — now click on the target company`);
        } else if (companyId === this.arrowSourceId) {
            // Clicked same company — deselect
            this.arrowSourceId = null;
            document.querySelectorAll('.company-node.arrow-source').forEach(el => el.classList.remove('arrow-source'));
            this.showStatusBar('Click on the source company');
        } else {
            // Second click — pick target, open modal
            this.arrowTargetId = companyId;
            this.openArrowModal();
        }
    }

    openArrowModal() {
        const from = this.state.getCompany(this.arrowSourceId);
        const to = this.state.getCompany(this.arrowTargetId);
        document.getElementById('arrow-modal-info').textContent = `${from?.name || '?'}  →  ${to?.name || '?'}`;
        document.getElementById('arrow-label').value = '';
        this.arrowModal.classList.remove('hidden');
        document.getElementById('arrow-label').focus();
    }

    closeArrowModal() {
        this.arrowModal.classList.add('hidden');
        this.arrowTargetId = null;
        // stay in arrow mode so user can keep drawing
        this.arrowSourceId = null;
        document.querySelectorAll('.company-node.arrow-source').forEach(el => el.classList.remove('arrow-source'));
        this.showStatusBar('Click on the source company');
    }

    handleArrowSubmit() {
        const label = document.getElementById('arrow-label').value.trim();
        if (this.arrowSourceId && this.arrowTargetId) {
            this.state.addArrow(this.arrowSourceId, this.arrowTargetId, label);
            this.renderArrows();
        }
        this.arrowModal.classList.add('hidden');
        // Reset for next arrow
        this.arrowSourceId = null;
        this.arrowTargetId = null;
        document.querySelectorAll('.company-node.arrow-source').forEach(el => el.classList.remove('arrow-source'));
        this.showStatusBar('Arrow created! Click on a source company for another, or press Esc');
    }

    showStatusBar(text) {
        this.removeStatusBar();
        const bar = document.createElement('div');
        bar.className = 'arrow-status-bar';
        bar.textContent = text;
        this.mapContainer.appendChild(bar);
        this.statusBar = bar;
    }

    removeStatusBar() {
        if (this.statusBar) {
            this.statusBar.remove();
            this.statusBar = null;
        }
    }

    // ===== Arrow Rendering =====

    renderArrows() {
        // Keep the <defs> but remove all other children
        const defs = this.arrowsLayer.querySelector('defs');
        this.arrowsLayer.innerHTML = '';
        if (defs) this.arrowsLayer.appendChild(defs);

        this.state.arrows.forEach(arrow => {
            const from = this.state.getCompany(arrow.fromId);
            const to = this.state.getCompany(arrow.toId);
            if (!from || !to) return;

            // Get node dimensions to offset the arrow end from center
            const fromEl = document.getElementById(`node-${from.id}`);
            const toEl = document.getElementById(`node-${to.id}`);
            const fromW = fromEl ? fromEl.offsetWidth / 2 : 40;
            const fromH = fromEl ? fromEl.offsetHeight / 2 : 16;
            const toW = toEl ? toEl.offsetWidth / 2 : 40;
            const toH = toEl ? toEl.offsetHeight / 2 : 16;

            // Direction vector
            const dx = to.x - from.x;
            const dy = to.y - from.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist === 0) return;
            const ux = dx / dist;
            const uy = dy / dist;

            // Ellipse-based offset for pill-shaped nodes
            const startOffset = Math.sqrt((fromW * ux) ** 2 + (fromH * uy) ** 2) + 4;
            const endOffset = Math.sqrt((toW * ux) ** 2 + (toH * uy) ** 2) + 6;

            const x1 = from.x + ux * startOffset;
            const y1 = from.y + uy * startOffset;
            const x2 = to.x - ux * endOffset;
            const y2 = to.y - uy * endOffset;

            const ns = 'http://www.w3.org/2000/svg';

            // Invisible wider hit area for easier clicking
            const hitArea = document.createElementNS(ns, 'line');
            hitArea.setAttribute('x1', x1);
            hitArea.setAttribute('y1', y1);
            hitArea.setAttribute('x2', x2);
            hitArea.setAttribute('y2', y2);
            hitArea.setAttribute('class', 'arrow-hit-area');
            hitArea.dataset.arrowId = arrow.id;
            this.arrowsLayer.appendChild(hitArea);

            // Visible arrow line
            const line = document.createElementNS(ns, 'line');
            line.setAttribute('x1', x1);
            line.setAttribute('y1', y1);
            line.setAttribute('x2', x2);
            line.setAttribute('y2', y2);
            line.setAttribute('marker-end', 'url(#arrowhead)');
            line.setAttribute('class', 'arrow-line');
            line.dataset.arrowId = arrow.id;
            this.arrowsLayer.appendChild(line);

            // Midpoint for label / delete
            const mx = (x1 + x2) / 2;
            const my = (y1 + y2) / 2;

            // Label group (always rendered for delete button; label text optional)
            const labelGroup = document.createElementNS(ns, 'g');
            labelGroup.setAttribute('class', 'arrow-label-group');

            if (arrow.label) {
                // Approximate text width
                const textWidth = arrow.label.length * 6.5 + 16;
                const textHeight = 22;

                const bg = document.createElementNS(ns, 'rect');
                bg.setAttribute('x', mx - textWidth / 2);
                bg.setAttribute('y', my - textHeight / 2);
                bg.setAttribute('width', textWidth);
                bg.setAttribute('height', textHeight);
                bg.setAttribute('class', 'arrow-label-bg');
                labelGroup.appendChild(bg);

                const txt = document.createElementNS(ns, 'text');
                txt.setAttribute('x', mx);
                txt.setAttribute('y', my);
                txt.setAttribute('class', 'arrow-label-text');
                txt.textContent = arrow.label;
                labelGroup.appendChild(txt);
            }

            // Delete circle
            const delOffset = arrow.label ? (arrow.label.length * 6.5 + 16) / 2 + 14 : 0;
            const delX = arrow.label ? mx + delOffset : mx;
            const delY = arrow.label ? my : my;

            const delCircle = document.createElementNS(ns, 'circle');
            delCircle.setAttribute('cx', delX);
            delCircle.setAttribute('cy', delY);
            delCircle.setAttribute('r', 9);
            delCircle.setAttribute('class', 'arrow-delete-circle');
            labelGroup.appendChild(delCircle);

            // X icon inside delete circle
            const xLine1 = document.createElementNS(ns, 'line');
            xLine1.setAttribute('x1', delX - 3); xLine1.setAttribute('y1', delY - 3);
            xLine1.setAttribute('x2', delX + 3); xLine1.setAttribute('y2', delY + 3);
            xLine1.setAttribute('class', 'arrow-delete-x');
            labelGroup.appendChild(xLine1);

            const xLine2 = document.createElementNS(ns, 'line');
            xLine2.setAttribute('x1', delX + 3); xLine2.setAttribute('y1', delY - 3);
            xLine2.setAttribute('x2', delX - 3); xLine2.setAttribute('y2', delY + 3);
            xLine2.setAttribute('class', 'arrow-delete-x');
            labelGroup.appendChild(xLine2);

            // Delete click handler
            delCircle.addEventListener('click', (e) => {
                e.stopPropagation();
                this.state.removeArrow(arrow.id);
                this.renderArrows();
            });

            this.arrowsLayer.appendChild(labelGroup);
        });
    }
}

// Initialize Application
document.addEventListener('DOMContentLoaded', () => {
    const state = new StateManager();
    const ui = new UIController(state);
    ui.initGroupEventListeners();
    // Render saved groups from localStorage
    state.groups.forEach(g => ui.renderGroupShape(g));
    // Render saved arrows from localStorage
    ui.renderArrows();
});
