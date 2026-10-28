const state = {
      runs: [],
      selectedRunId: null,
      detail: null,
      level: 'window',
      metric: 'auroc',
      timeMode: 'train_eval',
      source: 'all',
      target: 'all',
      query: '',
      modelQuery: '',
      explorer: null,
      explorerDatasetQuery: '',
      explorerModelQuery: '',
      cacheEntries: [],
      cacheDataset: 'all',
      runnerOptions: null,
      jobs: [],
      activeJobId: null,
      runnerSource: 'package',
      runnerConfig: null,
      builderOptions: null,
      builderConfig: null,
      builderPreset: 'custom',
      builderModelParams: {},
      builderAdvancedParams: {},
      validation: null
    };

    const FALLBACK_LEAD_OPTIONS = ['II', 'MLII', 'I', 'V1', 'ECG'];
    const TERMINAL_JOB_STATUSES = new Set(['succeeded', 'failed', 'cancelled', 'removed']);

    const fmtNumber = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 });
    const fmtDate = new Intl.DateTimeFormat(undefined, {
      year: 'numeric', month: 'short', day: '2-digit',
      hour: '2-digit', minute: '2-digit'
    });

    const els = {
      artifactRoot: document.getElementById('artifactRoot'),
      viewTabs: document.querySelectorAll('[data-view]'),
      sidebarPanels: document.querySelectorAll('[data-sidebar-panel]'),
      resultsView: document.getElementById('resultsView'),
      runnerView: document.getElementById('runnerView'),
      explorerView: document.getElementById('explorerView'),
      cacheView: document.getElementById('cacheView'),
      builderView: document.getElementById('builderView'),
      runList: document.getElementById('runList'),
      runErrors: document.getElementById('runErrors'),
      refreshButton: document.getElementById('refreshButton'),
      runSearch: document.getElementById('runSearch'),
      experimentName: document.getElementById('experimentName'),
      selectedRunId: document.getElementById('selectedRunId'),
      statusPill: document.getElementById('statusPill'),
      elapsedStat: document.getElementById('elapsedStat'),
      runTimeStat: document.getElementById('runTimeStat'),
      windowBestStat: document.getElementById('windowBestStat'),
      windowBestDetail: document.getElementById('windowBestDetail'),
      recordingBestStat: document.getElementById('recordingBestStat'),
      recordingBestDetail: document.getElementById('recordingBestDetail'),
      modelsStat: document.getElementById('modelsStat'),
      datasetsStat: document.getElementById('datasetsStat'),
      windowingStat: document.getElementById('windowingStat'),
      samplingStat: document.getElementById('samplingStat'),
      metricRowsStat: document.getElementById('metricRowsStat'),
      artifactCountStat: document.getElementById('artifactCountStat'),
      metricSelect: document.getElementById('metricSelect'),
      sourceSelect: document.getElementById('sourceSelect'),
      targetSelect: document.getElementById('targetSelect'),
      modelSearch: document.getElementById('modelSearch'),
      selectedMetricHead: document.getElementById('selectedMetricHead'),
      metricTableBody: document.getElementById('metricTableBody'),
      chartArea: document.getElementById('chartArea'),
      timeModeSelect: document.getElementById('timeModeSelect'),
      timeChartArea: document.getElementById('timeChartArea'),
      distributionChartArea: document.getElementById('distributionChartArea'),
      runDetails: document.getElementById('runDetails'),
      datasetChips: document.getElementById('datasetChips'),
      modelChips: document.getElementById('modelChips'),
      artifactTableBody: document.getElementById('artifactTableBody'),
      deleteRunButton: document.getElementById('deleteRunButton'),
      refreshExplorerButton: document.getElementById('refreshExplorerButton'),
      explorerErrors: document.getElementById('explorerErrors'),
      explorerStatusPill: document.getElementById('explorerStatusPill'),
      datasetExplorerSearch: document.getElementById('datasetExplorerSearch'),
      modelExplorerSearch: document.getElementById('modelExplorerSearch'),
      datasetExplorerStat: document.getElementById('datasetExplorerStat'),
      modelExplorerStat: document.getElementById('modelExplorerStat'),
      datasetExplorerBody: document.getElementById('datasetExplorerBody'),
      modelExplorerBody: document.getElementById('modelExplorerBody'),
      refreshJobsButton: document.getElementById('refreshJobsButton'),
      runnerErrors: document.getElementById('runnerErrors'),
      runnerQueueSummary: document.getElementById('runnerQueueSummary'),
      jobList: document.getElementById('jobList'),
      runnerStatusPill: document.getElementById('runnerStatusPill'),
      runnerSourceSelect: document.getElementById('runnerSourceSelect'),
      runnerPackageSelect: document.getElementById('runnerPackageSelect'),
      runnerS3Select: document.getElementById('runnerS3Select'),
      runnerS3KeyInput: document.getElementById('runnerS3KeyInput'),
      runnerUploadInput: document.getElementById('runnerUploadInput'),
      runnerRunIdInput: document.getElementById('runnerRunIdInput'),
      loadRunnerConfigButton: document.getElementById('loadRunnerConfigButton'),
      startRunnerJobButton: document.getElementById('startRunnerJobButton'),
      runnerValidationBox: document.getElementById('runnerValidationBox'),
      runnerConfigPreview: document.getElementById('runnerConfigPreview'),
      runnerCommandPreview: document.getElementById('runnerCommandPreview'),
      cancelRunnerJobButton: document.getElementById('cancelRunnerJobButton'),
      activeJobSummary: document.getElementById('activeJobSummary'),
      runnerProgressFill: document.getElementById('runnerProgressFill'),
      stageList: document.getElementById('stageList'),
      queueTableBody: document.getElementById('queueTableBody'),
      historyTableBody: document.getElementById('historyTableBody'),
      cacheDatasetFilter: document.getElementById('cacheDatasetFilter'),
      refreshCacheButton: document.getElementById('refreshCacheButton'),
      cacheErrors: document.getElementById('cacheErrors'),
      cacheRoot: document.getElementById('cacheRoot'),
      cacheStatusPill: document.getElementById('cacheStatusPill'),
      cacheTableBody: document.getElementById('cacheTableBody'),
      builderStatusPill: document.getElementById('builderStatusPill'),
      presetSelect: document.getElementById('presetSelect'),
      filenameInput: document.getElementById('filenameInput'),
      builderWarnings: document.getElementById('builderWarnings'),
      experimentNameInput: document.getElementById('experimentNameInput'),
      randomSeedInput: document.getElementById('randomSeedInput'),
      sparkAppInput: document.getElementById('sparkAppInput'),
      experimentDescriptionInput: document.getElementById('experimentDescriptionInput'),
      sourceDatasetCheckboxes: document.getElementById('sourceDatasetCheckboxes'),
      targetDatasetCheckboxes: document.getElementById('targetDatasetCheckboxes'),
      sourceDatasetManualInput: document.getElementById('sourceDatasetManualInput'),
      targetDatasetManualInput: document.getElementById('targetDatasetManualInput'),
      addSourceDatasetButton: document.getElementById('addSourceDatasetButton'),
      addTargetDatasetButton: document.getElementById('addTargetDatasetButton'),
      sampleRateInput: document.getElementById('sampleRateInput'),
      windowSecondsInput: document.getElementById('windowSecondsInput'),
      strideSecondsInput: document.getElementById('strideSecondsInput'),
      labelPolicySelect: document.getElementById('labelPolicySelect'),
      preferredLeadCheckboxes: document.getElementById('preferredLeadCheckboxes'),
      preferredLeadManualInput: document.getElementById('preferredLeadManualInput'),
      addPreferredLeadButton: document.getElementById('addPreferredLeadButton'),
      trainRatioInput: document.getElementById('trainRatioInput'),
      valRatioInput: document.getElementById('valRatioInput'),
      testRatioInput: document.getElementById('testRatioInput'),
      stratifySelect: document.getElementById('stratifySelect'),
      recordingAggregationSelect: document.getElementById('recordingAggregationSelect'),
      recordingLabelPolicySelect: document.getElementById('recordingLabelPolicySelect'),
      recordingThresholdInput: document.getElementById('recordingThresholdInput'),
      metricsCheckboxes: document.getElementById('metricsCheckboxes'),
      modelCheckboxes: document.getElementById('modelCheckboxes'),
      modelEditors: document.getElementById('modelEditors'),
      validateConfigButton: document.getElementById('validateConfigButton'),
      downloadConfigButton: document.getElementById('downloadConfigButton'),
      saveConfigS3Button: document.getElementById('saveConfigS3Button'),
      connectionCheckInput: document.getElementById('connectionCheckInput'),
      validationBox: document.getElementById('validationBox'),
      commandPreview: document.getElementById('commandPreview'),
      yamlPreview: document.getElementById('yamlPreview')
    };

    async function loadRuns() {
      els.runList.innerHTML = '<div class="empty">Loading runs</div>';
      const response = await fetch('/api/runs?limit=100');
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not load runs');
      els.artifactRoot.textContent = payload.artifact_root || 'Artifact root unavailable';
      els.runErrors.innerHTML = (payload.errors || []).map(error => `<div class="error">${escapeHtml(error)}</div>`).join('');
      state.runs = payload.runs || [];
      renderRunList();
      const selectedAvailable = state.runs.some(run => run.run_id === state.selectedRunId);
      if ((!state.selectedRunId || !selectedAvailable) && state.runs.length) {
        await selectRun(state.runs[0].run_id);
      } else if (state.selectedRunId && selectedAvailable) {
        await selectRun(state.selectedRunId);
      } else {
        state.selectedRunId = null;
        state.detail = null;
        renderEmptyRunDetail();
      }
    }

    async function selectRun(runId) {
      state.selectedRunId = runId;
      renderRunList();
      els.metricTableBody.innerHTML = '<tr><td colspan="11">Loading</td></tr>';
      const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not load run');
      state.detail = payload;
      syncFilterOptions();
      renderDetail();
    }

    async function loadBuilderOptions() {
      const response = await fetch('/api/config/options');
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not load config options');
      state.builderOptions = payload;
      renderBuilderStaticControls();
      const preferredPreset = payload.presets.find(preset => preset.id === 'baseline') || payload.presets[0];
      applyBuilderPreset(preferredPreset ? preferredPreset.id : 'custom');
    }

    async function loadRunnerOptions() {
      const response = await fetch('/api/runner/options');
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not load runner options');
      state.runnerOptions = payload;
      renderRunnerConfigControls();
    }

    async function loadJobs() {
      const response = await fetch('/api/jobs');
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not load experiment jobs');
      state.jobs = payload.jobs || [];
      if (payload.active) state.activeJobId = payload.active.job_id;
      else if (!state.jobs.some(job => job.job_id === state.activeJobId)) state.activeJobId = null;
      renderJobs(payload);
    }

    async function loadCache() {
      els.cacheTableBody.innerHTML = '<tr><td colspan="8">Loading</td></tr>';
      const query = state.cacheDataset && state.cacheDataset !== 'all'
        ? `?dataset=${encodeURIComponent(state.cacheDataset)}`
        : '';
      const response = await fetch(`/api/cache${query}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not load cache entries');
      els.cacheRoot.textContent = payload.cache_root || 'Cache root unavailable';
      els.cacheErrors.innerHTML = (payload.errors || []).map(error => `<div class="error">${escapeHtml(error)}</div>`).join('');
      state.cacheEntries = payload.entries || [];
      renderCacheDatasetFilter(payload.datasets || []);
      renderCacheTable();
    }

    async function loadExplorer() {
      els.datasetExplorerBody.innerHTML = '<tr><td colspan="7">Loading</td></tr>';
      els.modelExplorerBody.innerHTML = '<tr><td colspan="6">Loading</td></tr>';
      const response = await fetch('/api/explorer');
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not load explorer data');
      state.explorer = payload;
      els.explorerErrors.innerHTML = (payload.warnings || []).map(warning => `<div class="error">${escapeHtml(warning)}</div>`).join('');
      renderExplorer();
    }

    function renderBuilderStaticControls() {
      const options = state.builderOptions || {};
      fillSelect(
        els.presetSelect,
        (options.presets || []).map(preset => preset.id),
        state.builderPreset,
        id => ((options.presets || []).find(preset => preset.id === id) || {}).label || id
      );
      fillSelect(els.stratifySelect, (options.enums && options.enums.split_stratifications) || [], 'dataset_af_present', value => value);
      fillSelect(els.labelPolicySelect, (options.enums && options.enums.label_policies) || ['af_vs_nsr'], 'af_vs_nsr', value => value);
      fillSelect(els.recordingAggregationSelect, (options.enums && options.enums.recording_aggregations) || [], 'mean', value => value);
      fillSelect(els.recordingLabelPolicySelect, (options.enums && options.enums.recording_label_policies) || [], 'fraction_threshold', value => value);
      els.builderWarnings.innerHTML = (options.warnings || []).map(warning => escapeHtml(warning)).join('<br>');
      renderDatasetCheckboxes([], []);
      renderPreferredLeadCheckboxes((options.default_preferred_leads || FALLBACK_LEAD_OPTIONS));
      renderMetricCheckboxes([]);
      renderModelCheckboxes([]);
    }

    function renderRunnerConfigControls() {
      const options = state.runnerOptions || {};
      const packageExamples = options.package_examples || [];
      const s3Configs = options.s3_configs || [];
      const packageValues = packageExamples.map(item => item.name);
      fillSelect(
        els.runnerPackageSelect,
        packageValues.length ? packageValues : [''],
        packageValues[0] || '',
        value => value || 'No package examples found'
      );
      els.runnerPackageSelect.disabled = !packageValues.length;
      fillSelect(
        els.runnerS3Select,
        ['manual', ...s3Configs.map(item => item.key)],
        'manual',
        value => value === 'manual' ? 'Enter key manually' : value
      );
      els.runnerS3KeyInput.placeholder = `${options.s3_config_prefix || 'benchmarks/configs'}/showcase_smoke.yaml`;
      syncRunnerSourceFields();
    }

    function syncRunnerSourceFields() {
      state.runnerSource = els.runnerSourceSelect.value || 'package';
      document.querySelectorAll('.runner-source-field').forEach(field => {
        field.style.display = field.dataset.runnerSource === state.runnerSource ? 'grid' : 'none';
      });
    }

    function applyBuilderPreset(presetId) {
      const options = state.builderOptions || {};
      const preset = (options.presets || []).find(item => item.id === presetId) || (options.presets || [])[0];
      if (!preset) return;
      state.builderPreset = preset.id;
      state.builderConfig = deepClone(preset.config);
      state.builderModelParams = {};
      state.builderAdvancedParams = {};
      els.presetSelect.value = preset.id;
      populateBuilderForm(state.builderConfig);
      renderBuilderDraft();
    }

    function populateBuilderForm(config) {
      const experiment = config.experiment || {};
      const data = config.data || {};
      const split = data.split || {};
      const spark = config.spark || {};
      els.experimentNameInput.value = experiment.name || '';
      els.experimentDescriptionInput.value = experiment.description || '';
      els.randomSeedInput.value = config.random_seed ?? '';
      els.sparkAppInput.value = spark.app_name || '';
      renderDatasetCheckboxes(data.source_datasets || [], data.target_datasets || []);
      els.sampleRateInput.value = data.sample_rate_hz ?? 250;
      els.windowSecondsInput.value = data.window_seconds ?? 10;
      els.strideSecondsInput.value = data.stride_seconds ?? 5;
      els.labelPolicySelect.value = data.label_policy || 'af_vs_nsr';
      renderPreferredLeadCheckboxes(data.preferred_leads || []);
      els.trainRatioInput.value = split.train ?? 0.7;
      els.valRatioInput.value = split.val ?? 0.15;
      els.testRatioInput.value = split.test ?? 0.15;
      els.stratifySelect.value = split.stratify_by || 'dataset_af_present';
      els.recordingAggregationSelect.value = data.recording_aggregation || 'mean';
      els.recordingLabelPolicySelect.value = data.recording_label_policy || 'fraction_threshold';
      els.recordingThresholdInput.value = data.recording_label_threshold ?? 0.5;
      const selectedMetrics = config.metrics || [];
      renderMetricCheckboxes(selectedMetrics);
      const selectedModels = (config.models || []).map(item => {
        const modelId = typeof item === 'string' ? item : item.id;
        state.builderModelParams[modelId] = deepClone((typeof item === 'string' ? {} : item.params) || {});
        return modelId;
      }).filter(Boolean);
      renderModelCheckboxes(selectedModels);
      renderModelEditors();
      els.filenameInput.value = `${sanitizeClientFilename(experiment.name || 'benchmark-config')}.yaml`;
    }

    function renderMetricCheckboxes(selected) {
      const metrics = (state.builderOptions && state.builderOptions.metrics) || [];
      renderMultiSelect(els.metricsCheckboxes, {
        options: metrics.map(metric => ({ value: metric, label: metricLabel(metric) })),
        selected,
        placeholder: 'Search metrics',
        emptyMessage: 'No metrics available.',
        onChange: renderBuilderDraft
      });
    }

    function renderModelCheckboxes(selected) {
      const models = (state.builderOptions && state.builderOptions.models) || [];
      renderMultiSelect(els.modelCheckboxes, {
        options: models.map(model => ({
          value: model.model_id,
          label: model.model_id,
          title: model.description || ''
        })),
        selected,
        placeholder: 'Search models',
        emptyMessage: 'No models available.',
        onChange: values => {
          values.forEach(modelId => {
            if (!state.builderModelParams[modelId]) state.builderModelParams[modelId] = {};
          });
          renderModelEditors();
          renderBuilderDraft();
        }
      });
    }

    function renderDatasetCheckboxes(sourceSelected, targetSelected) {
      const options = datasetOptionValues(sourceSelected, targetSelected);
      renderValueMultiSelect(
        els.sourceDatasetCheckboxes,
        options,
        sourceSelected,
        'Search source datasets',
        'No catalog datasets loaded. Add a source dataset manually.'
      );
      renderValueMultiSelect(
        els.targetDatasetCheckboxes,
        options,
        targetSelected,
        'Search target datasets',
        'No catalog datasets loaded. Add a target dataset manually.'
      );
    }

    function renderPreferredLeadCheckboxes(selected) {
      const selectedValues = selected || [];
      const optionValues = uniquePreserve([
        ...((state.builderOptions && (state.builderOptions.lead_options || state.builderOptions.default_preferred_leads)) || []),
        ...FALLBACK_LEAD_OPTIONS,
        ...selectedValues
      ]);
      renderValueMultiSelect(
        els.preferredLeadCheckboxes,
        optionValues,
        selectedValues,
        'Search leads',
        'Add at least one preferred lead.'
      );
    }

    function renderValueMultiSelect(container, values, selected, placeholder, emptyMessage) {
      renderMultiSelect(container, {
        options: values.map(value => ({ value, label: value })),
        selected,
        placeholder,
        emptyMessage,
        onChange: renderBuilderDraft
      });
    }

    function renderMultiSelect(container, config) {
      const options = config.options || [];
      const selectedValues = uniquePreserve(config.selected || []).filter(Boolean);
      const selectedSet = new Set(selectedValues);
      const optionMap = new Map(options.map(option => [option.value, option]));
      selectedValues.forEach(value => {
        if (!optionMap.has(value)) optionMap.set(value, { value, label: value });
      });
      const allOptions = Array.from(optionMap.values());

      container.classList.add('multi-select');
      container.innerHTML = `
        <div class="multi-select-box" role="combobox" aria-expanded="false" aria-haspopup="listbox">
          <div class="multi-select-tags">
            ${selectedValues.map(value => {
              const option = optionMap.get(value) || { label: value };
              return `<span class="multi-select-tag" data-selected-value="${escapeAttr(value)}"><span>${escapeHtml(option.label)}</span><button class="multi-select-remove" type="button" aria-label="Remove ${escapeAttr(option.label)}">x</button></span>`;
            }).join('')}
          </div>
          <input class="multi-select-input" type="search" placeholder="${escapeAttr(config.placeholder || 'Search')}" aria-label="${escapeAttr(config.placeholder || 'Search')}">
        </div>
        <div class="multi-select-menu" role="listbox" aria-multiselectable="true"></div>
      `;

      const input = container.querySelector('.multi-select-input');
      const box = container.querySelector('.multi-select-box');
      const menu = container.querySelector('.multi-select-menu');

      const close = () => {
        container.classList.remove('open');
        box.setAttribute('aria-expanded', 'false');
      };
      const open = () => {
        container.classList.add('open');
        box.setAttribute('aria-expanded', 'true');
        renderOptions();
      };
      const update = (values, keepOpen = false) => {
        close();
        renderMultiSelect(container, { ...config, selected: values });
        if (keepOpen) {
          const nextInput = container.querySelector('.multi-select-input');
          const nextBox = container.querySelector('.multi-select-box');
          container.classList.add('open');
          if (nextBox) nextBox.setAttribute('aria-expanded', 'true');
          if (nextInput) nextInput.focus();
        }
        if (config.onChange) config.onChange(values);
      };
      const toggleValue = value => {
        const values = selectedControlValues(container);
        const index = values.indexOf(value);
        if (index >= 0) values.splice(index, 1);
        else values.push(value);
        update(values, true);
      };
      const renderOptions = () => {
        const query = input.value.trim().toLowerCase();
        const filtered = allOptions.filter(option => {
          const haystack = `${option.label || option.value} ${option.value}`.toLowerCase();
          return !query || haystack.includes(query);
        });
        menu.innerHTML = filtered.length
          ? filtered.map(option => {
            const selectedClass = selectedSet.has(option.value) ? ' selected' : '';
            const title = option.title ? ` title="${escapeAttr(option.title)}"` : '';
            return `<button class="multi-select-option${selectedClass}" type="button" role="option" aria-selected="${selectedSet.has(option.value) ? 'true' : 'false'}" data-option-value="${escapeAttr(option.value)}"${title}>${escapeHtml(option.label || option.value)}</button>`;
          }).join('')
          : `<div class="multi-select-empty">${escapeHtml(config.emptyMessage || 'No options match your search.')}</div>`;
        menu.querySelectorAll('[data-option-value]').forEach(optionButton => {
          optionButton.addEventListener('click', () => toggleValue(optionButton.dataset.optionValue));
        });
      };

      box.addEventListener('click', () => {
        input.focus();
        open();
      });
      input.addEventListener('focus', open);
      input.addEventListener('input', renderOptions);
      input.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
          close();
          input.blur();
        }
        if (event.key === 'Backspace' && !input.value) {
          const values = selectedControlValues(container);
          if (values.length) {
            values.pop();
            update(values);
          }
        }
      });
      container.querySelectorAll('.multi-select-remove').forEach(button => {
        button.addEventListener('click', event => {
          event.stopPropagation();
          const value = button.closest('[data-selected-value]').dataset.selectedValue;
          update(selectedControlValues(container).filter(item => item !== value));
        });
      });
      renderOptions();
    }

    function datasetOptionValues(sourceSelected, targetSelected) {
      return uniquePreserve([
        ...((state.builderOptions && state.builderOptions.datasets) || []),
        ...(sourceSelected || []),
        ...(targetSelected || [])
      ]);
    }

    function renderModelEditors() {
      saveModelEditorState();
      const selected = selectedModelIds();
      const models = (state.builderOptions && state.builderOptions.models) || [];
      if (!selected.length) {
        els.modelEditors.innerHTML = '<div class="empty">Select at least one model</div>';
        return;
      }
      els.modelEditors.innerHTML = selected.map(modelId => {
        const info = models.find(item => item.model_id === modelId) || { configurable_parameters: {} };
        const params = state.builderModelParams[modelId] || {};
        const common = Object.entries(info.configurable_parameters || {});
        const commonHtml = common.length ? common.map(([name, description]) => {
          const value = params[name];
          const isFeatureColumns = name === 'feature_columns';
          const control = isFeatureColumns
            ? `<textarea class="control textarea" data-model-param="${escapeAttr(name)}" data-model-id="${escapeAttr(modelId)}">${escapeHtml(Array.isArray(value) ? value.join('\n') : value ?? '')}</textarea>`
            : `<input class="control" data-model-param="${escapeAttr(name)}" data-model-id="${escapeAttr(modelId)}" type="text" value="${escapeAttr(value ?? '')}">`;
          return `<div class="field"><label>${escapeHtml(name)}</label>${control}<p class="small muted">${escapeHtml(description)}</p></div>`;
        }).join('') : '<p class="small muted">This model has no declared common params.</p>';
        const advanced = state.builderAdvancedParams[modelId] || '{}';
        return `<div class="model-editor" data-editor-model="${escapeAttr(modelId)}">
          <h3>${escapeHtml(modelId)}</h3>
          <p class="small muted">${escapeHtml(info.description || '')}</p>
          <div class="param-grid">${commonHtml}</div>
          <div class="field" style="margin-top:10px;">
            <label>Advanced params JSON</label>
            <textarea class="control textarea" data-advanced-model="${escapeAttr(modelId)}">${escapeHtml(advanced)}</textarea>
          </div>
        </div>`;
      }).join('');
      els.modelEditors.querySelectorAll('[data-model-param], [data-advanced-model]').forEach(input => {
        input.addEventListener('input', renderBuilderDraft);
      });
    }

    function saveModelEditorState() {
      if (!els.modelEditors) return;
      els.modelEditors.querySelectorAll('[data-editor-model]').forEach(editor => {
        const modelId = editor.dataset.editorModel;
        const params = state.builderModelParams[modelId] || {};
        editor.querySelectorAll('[data-model-param]').forEach(input => {
          const name = input.dataset.modelParam;
          if (name === 'feature_columns') {
            const values = splitList(input.value);
            if (values.length) params[name] = values;
            else delete params[name];
          } else if (input.value.trim()) {
            params[name] = parseScalar(input.value.trim());
          } else {
            delete params[name];
          }
        });
        const advanced = editor.querySelector('[data-advanced-model]');
        if (advanced) state.builderAdvancedParams[modelId] = advanced.value;
        state.builderModelParams[modelId] = params;
      });
    }

    function buildBuilderConfig() {
      saveModelEditorState();
      const base = deepClone(state.builderConfig || (state.builderOptions && state.builderOptions.defaults) || {});
      const sourceDatasets = selectedControlValues(els.sourceDatasetCheckboxes);
      const targetDatasets = selectedControlValues(els.targetDatasetCheckboxes);
      const selectedMetrics = selectedControlValues(els.metricsCheckboxes);
      const models = selectedModelIds().map(modelId => {
        const params = deepClone(state.builderModelParams[modelId] || {});
        const advancedText = (state.builderAdvancedParams[modelId] || '{}').trim() || '{}';
        let advancedParams = {};
        try {
          advancedParams = JSON.parse(advancedText);
        } catch (error) {
          throw new Error(`${modelId} advanced params JSON is invalid: ${error.message}`);
        }
        if (!advancedParams || typeof advancedParams !== 'object' || Array.isArray(advancedParams)) {
          throw new Error(`${modelId} advanced params JSON must be an object`);
        }
        const merged = { ...params, ...advancedParams };
        return Object.keys(merged).length ? { id: modelId, params: merged } : { id: modelId };
      });
      return {
        random_seed: numberValue(els.randomSeedInput.value),
        experiment: {
          name: els.experimentNameInput.value.trim(),
          description: els.experimentDescriptionInput.value.trim() || null
        },
        data: {
          ...(base.data || {}),
          source_datasets: sourceDatasets,
          target_datasets: targetDatasets.length ? targetDatasets : sourceDatasets,
          sample_rate_hz: numberValue(els.sampleRateInput.value),
          window_seconds: numberValue(els.windowSecondsInput.value),
          stride_seconds: numberValue(els.strideSecondsInput.value),
          label_policy: els.labelPolicySelect.value,
          preferred_leads: selectedControlValues(els.preferredLeadCheckboxes),
          recording_aggregation: els.recordingAggregationSelect.value,
          recording_label_policy: els.recordingLabelPolicySelect.value,
          recording_label_threshold: numberValue(els.recordingThresholdInput.value),
          split: {
            train: numberValue(els.trainRatioInput.value),
            val: numberValue(els.valRatioInput.value),
            test: numberValue(els.testRatioInput.value),
            stratify_by: els.stratifySelect.value
          }
        },
        storage: base.storage || {},
        database: base.database || {},
        spark: {
          ...(base.spark || {}),
          app_name: els.sparkAppInput.value.trim() || 'ecg-af-benchmark'
        },
        reuse: base.reuse || {},
        models,
        metrics: selectedMetrics
      };
    }

    async function validateBuilderConfig() {
      try {
        const config = buildBuilderConfig();
        const payload = await postJson('/api/config/validate', {
          config,
          check_connections: els.connectionCheckInput.checked
        });
        state.validation = payload;
        renderValidation(payload);
      } catch (error) {
        renderValidation({ ok: false, errors: [error.message || String(error)], yaml: '' });
      }
    }

    async function downloadBuilderConfig() {
      try {
        const config = buildBuilderConfig();
        const payload = await postJson('/api/config/validate', {
          config,
          check_connections: els.connectionCheckInput.checked
        });
        state.validation = payload;
        renderValidation(payload);
        if (!payload.ok) return;
        const filename = normalizedConfigFilename(els.filenameInput.value.trim(), config);
        downloadText(filename, payload.yaml || toSimpleYaml(payload.resolved_config || config));
        els.commandPreview.textContent = commandPreviewForFilename(filename);
      } catch (error) {
        renderValidation({ ok: false, errors: [error.message || String(error)], yaml: els.yamlPreview.textContent });
      }
    }

    async function saveBuilderConfigToS3(overwrite = false) {
      try {
        const config = buildBuilderConfig();
        const filename = normalizedConfigFilename(els.filenameInput.value.trim(), config);
        const payload = await postJson('/api/config/upload', {
          config,
          filename,
          overwrite,
          check_connections: els.connectionCheckInput.checked
        });
        if (payload._status === 409 && !overwrite) {
          const ok = window.confirm(`${(payload.errors || ['Config already exists.'])[0]}\n\nOverwrite it?`);
          if (ok) return saveBuilderConfigToS3(true);
        }
        state.validation = payload;
        renderValidation(payload);
        if (payload.ok && payload.key) {
          els.commandPreview.textContent = `Run Experiment -> Source: MinIO/S3 config\nS3 key: ${payload.key}`;
        }
      } catch (error) {
        renderValidation({ ok: false, errors: [error.message || String(error)], yaml: els.yamlPreview.textContent });
      }
    }

    async function loadRunnerConfig() {
      try {
        const source = els.runnerSourceSelect.value || state.runnerSource || 'package';
        state.runnerSource = source;
        let payload = { source };
        if (source === 'package') {
          payload.name = els.runnerPackageSelect.value;
        } else if (source === 's3') {
          payload.key = els.runnerS3Select.value && els.runnerS3Select.value !== 'manual'
            ? els.runnerS3Select.value
            : els.runnerS3KeyInput.value.trim();
        } else if (source === 'upload') {
          const file = els.runnerUploadInput.files && els.runnerUploadInput.files[0];
          if (!file) throw new Error('Choose a YAML file first');
          payload.filename = file.name;
          payload.yaml = await readFileText(file);
        }
        const response = await postJson('/api/runner/config', payload);
        state.runnerConfig = response.ok ? response : null;
        renderRunnerValidation(response);
      } catch (error) {
        state.runnerConfig = null;
        renderRunnerValidation({ ok: false, errors: [error.message || String(error)] });
      }
    }

    async function startRunnerJob() {
      if (!state.runnerConfig || !state.runnerConfig.ok) return;
      const payload = await postJson('/api/jobs', {
        source: state.runnerConfig.source || state.runnerSource,
        filename: state.runnerConfig.filename || 'benchmark-config.yaml',
        yaml: state.runnerConfig.yaml || els.runnerConfigPreview.textContent,
        run_id: els.runnerRunIdInput.value.trim() || null
      });
      if (!payload.ok) {
        renderRunnerValidation({ ok: false, errors: payload.errors || ['Could not queue job'] });
        return;
      }
      state.activeJobId = payload.job && payload.job.job_id;
      await loadJobs();
    }

    async function deleteJob(jobId) {
      const ok = window.confirm('Delete this experiment from the dashboard queue/history? This does not delete benchmark results.');
      if (!ok) return;
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });
      const payload = await response.json();
      if (!response.ok || payload.ok === false) {
        throw new Error((payload.errors || [payload.error || 'Could not remove job']).join('\n'));
      }
      await loadJobs();
    }

    async function deleteSelectedRun() {
      const runId = state.selectedRunId;
      if (!runId) return;
      const run = (state.detail && state.detail.run) || state.runs.find(item => item.run_id === runId) || {};
      const ok = window.confirm(
        `Delete benchmark results for ${run.experiment_name || runId}?\n\nRun id: ${runId}\n\nThis removes the S3/MinIO artifacts and matching local dashboard run records.`
      );
      if (!ok) return;
      const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });
      const payload = await response.json();
      if (!response.ok || payload.ok === false) {
        throw new Error((payload.errors || [payload.error || 'Could not delete results']).join('\n'));
      }
      state.selectedRunId = null;
      await loadRuns();
      await loadJobs().catch(showRunnerError);
    }

    async function cancelActiveJob() {
      const active = activeJob();
      if (!active) return;
      const ok = window.confirm(`Cancel running experiment ${active.config_name}?`);
      if (!ok) return;
      const payload = await postJson(`/api/jobs/${encodeURIComponent(active.job_id)}/cancel`, {});
      if (!payload.ok) throw new Error((payload.errors || ['Could not cancel job']).join('\n'));
      await loadJobs();
    }

    async function postJson(url, body) {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const payload = await response.json();
      payload._status = response.status;
      if (!response.ok && !payload.errors) throw new Error(payload.error || 'Request failed');
      return payload;
    }

    function readFileText(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(reader.error || new Error('Could not read file'));
        reader.readAsText(file);
      });
    }

    function renderValidation(payload) {
      const ok = Boolean(payload.ok);
      els.validationBox.className = `validation ${ok ? 'ok' : 'bad'}`;
      els.validationBox.innerHTML = ok
        ? `Config valid. Hash: <strong>${escapeHtml(payload.config_hash || '')}</strong><br><span class="small">Artifact root: ${escapeHtml(payload.artifact_root_preview || '')}</span>${payload.s3_uri ? `<br><span class="small">Saved config: ${escapeHtml(payload.s3_uri)}</span>` : ''}`
        : `<strong>Config needs attention</strong><br>${(payload.errors || []).map(escapeHtml).join('<br>')}`;
      els.builderStatusPill.className = `pill ${ok ? 'succeeded' : 'failed'}`;
      els.builderStatusPill.textContent = ok ? 'valid' : 'invalid';
      if (payload.yaml) els.yamlPreview.textContent = payload.yaml;
      if (payload.command_examples) {
        els.commandPreview.textContent = `${payload.command_examples.validate}\n${payload.command_examples.run}`;
      } else if (ok) {
        els.commandPreview.textContent = commandPreviewForFilename(normalizedConfigFilename(els.filenameInput.value.trim(), payload.resolved_config || buildBuilderConfig()));
      }
    }

    function renderRunnerValidation(payload) {
      const ok = Boolean(payload.ok);
      els.runnerValidationBox.className = `validation ${ok ? 'ok' : 'bad'}`;
      els.runnerValidationBox.innerHTML = ok
        ? `Config valid. Hash: <strong>${escapeHtml(payload.config_hash || '')}</strong><br><span class="small">Artifact root: ${escapeHtml(payload.artifact_root_preview || '')}</span>`
        : `<strong>Config needs attention</strong><br>${(payload.errors || []).map(escapeHtml).join('<br>')}`;
      els.startRunnerJobButton.disabled = !ok;
      els.runnerConfigPreview.textContent = payload.yaml || '';
      if (ok) {
        const filename = payload.filename || 'benchmark-config.yaml';
        els.runnerCommandPreview.textContent = `uv run ecg-bench validate-config --config ${filename}\nuv run ecg-bench run --config ${filename}`;
      } else {
        els.runnerCommandPreview.textContent = 'Load a valid config to preview the command.';
      }
    }

    function renderJobs(payload) {
      const jobs = (payload && payload.jobs) || state.jobs || [];
      const active = payload && payload.active ? payload.active : activeJob();
      const queued = payload && payload.queued ? payload.queued : jobs.filter(job => job.status === 'queued');
      const history = payload && payload.history ? payload.history : jobs.filter(job => TERMINAL_JOB_STATUSES.has(job.status));
      els.runnerQueueSummary.textContent = `${queued.length} queued · ${history.length} finished`;
      els.runnerStatusPill.className = `pill ${active ? 'incomplete' : queued.length ? 'unknown' : 'succeeded'}`;
      els.runnerStatusPill.textContent = active ? 'running' : queued.length ? 'queued' : 'idle';
      renderJobList(jobs);
      renderActiveJob(active);
      renderQueueTable(queued);
      renderHistoryTable(history);
    }

    function renderJobList(jobs) {
      if (!jobs.length) {
        els.jobList.innerHTML = '<div class="empty">No jobs yet</div>';
        return;
      }
      els.jobList.innerHTML = jobs.slice().reverse().slice(0, 10).map(job => {
        const active = job.job_id === state.activeJobId ? ' active' : '';
        return `<button type="button" class="run-card${active}" data-job="${escapeAttr(job.job_id)}">
          <div style="display:flex; justify-content:space-between; gap:10px; align-items:flex-start;">
            <div class="run-title">${escapeHtml(job.config_name || job.job_id)}</div>
            <span class="pill ${escapeAttr(jobStatusClass(job.status))}">${escapeHtml(job.status || 'unknown')}</span>
          </div>
          <div class="small muted" style="margin-top:6px;">${escapeHtml(job.job_id)}</div>
          <div class="small muted" style="margin-top:4px;">${escapeHtml(formatDate(job.submitted_at) || 'Time unknown')}</div>
        </button>`;
      }).join('');
      els.jobList.querySelectorAll('[data-job]').forEach(card => {
        card.addEventListener('click', () => {
          state.activeJobId = card.dataset.job;
          renderJobs({ jobs: state.jobs });
        });
      });
    }

    function renderActiveJob(active) {
      const selected = state.jobs.find(job => job.job_id === state.activeJobId) || active;
      if (!selected) {
        els.activeJobSummary.textContent = 'No active job.';
        els.runnerProgressFill.style.width = '0%';
        els.stageList.innerHTML = '<div class="empty">Stages will appear after a job starts.</div>';
        els.cancelRunnerJobButton.disabled = true;
        return;
      }
      const progress = selected.progress || {};
      const percent = metricNumber(progress.percent) || 0;
      const failureDetail = selected.status === 'failed'
        ? (selected.stderr_tail || selected.error || '')
        : '';
      els.activeJobSummary.innerHTML = `
        <strong>${escapeHtml(selected.config_name || selected.job_id)}</strong>
        <span class="muted"> · ${escapeHtml(selected.status || 'unknown')} · ${escapeHtml(progress.completed || 0)}/${escapeHtml(progress.total || 0)} stages</span>
        ${progress.run_id ? `<br><span class="muted">Run id: ${escapeHtml(progress.run_id)}</span>` : ''}
        ${failureDetail ? `<div class="job-error-tail"><strong>Failure detail</strong><pre>${escapeHtml(failureDetail)}</pre></div>` : ''}
      `;
      els.runnerProgressFill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
      els.cancelRunnerJobButton.disabled = selected.status !== 'running';
      renderStageList(progress.stages || [], selected);
    }

    function renderStageList(stages, job = null) {
      if (!stages.length) {
        const events = ((job && job.progress && job.progress.events) || []).slice().reverse();
        const failure = events.find(event => event.event === 'workflow_failed');
        const plan = events.find(event => event.event === 'plan');
        if (failure) {
          els.stageList.innerHTML = `<div class="stage-row failed">
            <span class="stage-dot"></span>
            <div>
              <div><strong>Workflow failed before first stage</strong></div>
              <div class="small muted">${escapeHtml(failure.error || 'No error detail was emitted.')}</div>
            </div>
          </div>`;
          return;
        }
        if (job && job.status === 'running') {
          const planned = plan && plan.total ? ` · ${escapeHtml(plan.total)} planned stages` : '';
          els.stageList.innerHTML = `<div class="stage-row running">
            <span class="stage-dot"></span>
            <div>
              <div><strong>Workflow starting</strong></div>
              <div class="small muted">Waiting for the first stage event${planned}</div>
            </div>
          </div>`;
          return;
        }
        els.stageList.innerHTML = '<div class="empty">No stage events were emitted.</div>';
        return;
      }
      els.stageList.innerHTML = stages.slice().reverse().map(stage => {
        const result = stage.result || {};
        const detail = [
          result.dataset_name,
          result.source_dataset && result.target_dataset ? `${result.source_dataset} to ${result.target_dataset}` : result.source_dataset,
          result.model_id,
          stageRowCountLabel(result),
          result.labeled_windows !== undefined ? `${formatMetric(result.labeled_windows, 'n')} windows` : null
        ].filter(Boolean).join(' · ');
        return `<div class="stage-row ${escapeAttr(jobStatusClass(stage.status))}">
          <span class="stage-dot"></span>
          <div>
            <div><strong>${escapeHtml(stage.label || stage.stage || '')}</strong></div>
            <div class="small muted">${escapeHtml(stage.status || 'running')}${detail ? ` · ${escapeHtml(detail)}` : ''}</div>
          </div>
        </div>`;
      }).join('');
    }

    function stageRowCountLabel(result) {
      if (result.row_count === undefined) return null;
      const rows = formatMetric(result.row_count, 'n');
      if (result.full_row_count !== undefined && Number(result.full_row_count) !== Number(result.row_count)) {
        return `${rows}/${formatMetric(result.full_row_count, 'n')} rows`;
      }
      return `${rows} rows`;
    }

    function renderQueueTable(rows) {
      if (!rows.length) {
        els.queueTableBody.innerHTML = '<tr><td colspan="5">No queued jobs</td></tr>';
        return;
      }
      els.queueTableBody.innerHTML = rows.map(job => `<tr>
        <td>${escapeHtml(job.config_name || '')}</td>
        <td><span class="pill ${escapeAttr(jobStatusClass(job.status))}">${escapeHtml(job.status)}</span></td>
        <td>${escapeHtml(formatDate(job.submitted_at) || '--')}</td>
        <td>${escapeHtml(job.run_id || '--')}</td>
        <td><button class="btn danger compact-action" type="button" data-delete-job="${escapeAttr(job.job_id)}">Delete</button></td>
      </tr>`).join('');
      els.queueTableBody.querySelectorAll('[data-delete-job]').forEach(button => {
        button.addEventListener('click', () => deleteJob(button.dataset.deleteJob).catch(showRunnerError));
      });
    }

    function renderHistoryTable(rows) {
      const sorted = rows.slice().reverse().slice(0, 30);
      if (!sorted.length) {
        els.historyTableBody.innerHTML = '<tr><td colspan="6">No finished jobs</td></tr>';
        return;
      }
      els.historyTableBody.innerHTML = sorted.map(job => {
        const runButton = job.run_id
          ? `<button class="btn compact-action" type="button" data-open-run="${escapeAttr(job.run_id)}">Open Results</button>`
          : '--';
        const deleteButton = `<button class="btn danger compact-action" type="button" data-delete-job="${escapeAttr(job.job_id)}">Delete</button>`;
        return `<tr>
          <td>${escapeHtml(job.config_name || '')}</td>
          <td><span class="pill ${escapeAttr(jobStatusClass(job.status))}">${escapeHtml(job.status)}</span></td>
          <td>${escapeHtml(formatDate(job.started_at) || '--')}</td>
          <td>${escapeHtml(formatDate(job.finished_at) || '--')}</td>
          <td>${escapeHtml(job.run_id || '--')}</td>
          <td><div class="action-stack">${runButton}${deleteButton}</div></td>
        </tr>`;
      }).join('');
      els.historyTableBody.querySelectorAll('[data-open-run]').forEach(button => {
        button.addEventListener('click', () => {
          activateView('resultsView');
          selectRun(button.dataset.openRun).catch(showError);
        });
      });
      els.historyTableBody.querySelectorAll('[data-delete-job]').forEach(button => {
        button.addEventListener('click', () => deleteJob(button.dataset.deleteJob).catch(showRunnerError));
      });
    }

    function renderBuilderDraft() {
      try {
        const config = buildBuilderConfig();
        els.yamlPreview.textContent = toSimpleYaml(config);
        els.validationBox.className = 'validation';
        els.validationBox.textContent = 'Draft config has not been validated yet.';
        els.builderStatusPill.className = 'pill unknown';
        els.builderStatusPill.textContent = 'draft';
      } catch (error) {
        els.validationBox.className = 'validation bad';
        els.validationBox.textContent = error.message || String(error);
      }
    }

    function selectedModelIds() {
      return selectedControlValues(els.modelCheckboxes);
    }

    function selectedControlValues(container) {
      return Array.from(container.querySelectorAll('[data-selected-value]'))
        .map(tag => tag.dataset.selectedValue)
        .filter(Boolean);
    }

    function closeMultiSelect(container) {
      container.classList.remove('open');
      const box = container.querySelector('.multi-select-box');
      if (box) box.setAttribute('aria-expanded', 'false');
    }

    function addManualDataset(kind) {
      const input = kind === 'source' ? els.sourceDatasetManualInput : els.targetDatasetManualInput;
      const value = input.value.trim();
      if (!value) return;
      const sources = selectedControlValues(els.sourceDatasetCheckboxes);
      const targets = selectedControlValues(els.targetDatasetCheckboxes);
      if (kind === 'source' && !sources.includes(value)) sources.push(value);
      if (kind === 'target' && !targets.includes(value)) targets.push(value);
      input.value = '';
      renderDatasetCheckboxes(sources, targets);
      renderBuilderDraft();
    }

    function addManualPreferredLead() {
      const value = els.preferredLeadManualInput.value.trim();
      if (!value) return;
      const leads = selectedControlValues(els.preferredLeadCheckboxes);
      if (!leads.includes(value)) leads.push(value);
      els.preferredLeadManualInput.value = '';
      renderPreferredLeadCheckboxes(leads);
      renderBuilderDraft();
    }

    function normalizedConfigFilename(value, config) {
      const experiment = (config && config.experiment && config.experiment.name) || 'benchmark-config';
      const base = sanitizeClientFilename(value || experiment);
      return base.endsWith('.yaml') || base.endsWith('.yml') ? base : `${base}.yaml`;
    }

    function commandPreviewForFilename(filename) {
      return `uv run ecg-bench validate-config --config ${filename}\nuv run ecg-bench run --config ${filename}`;
    }

    function downloadText(filename, text) {
      const blob = new Blob([text], { type: 'application/x-yaml;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }

    function renderRunList() {
      const query = state.query.trim().toLowerCase();
      const runs = state.runs.filter(run => !query || JSON.stringify(run).toLowerCase().includes(query));
      if (!runs.length) {
        els.runList.innerHTML = '<div class="empty">No runs found</div>';
        return;
      }
      els.runList.innerHTML = runs.map(run => {
        const active = run.run_id === state.selectedRunId ? ' active' : '';
        const status = run.status || 'unknown';
        const best = formatBest(run.best && (run.best.window || run.best.recording), true);
        return `<button type="button" class="run-card${active}" data-run="${escapeAttr(run.run_id)}">
          <div style="display:flex; justify-content:space-between; gap:10px; align-items:flex-start;">
            <div class="run-title">${escapeHtml(run.experiment_name || run.run_id)}</div>
            <span class="pill ${escapeAttr(status)}">${escapeHtml(status)}</span>
          </div>
          <div class="small muted" style="margin-top:6px;">${escapeHtml(run.run_id)}</div>
          <div class="small" style="margin-top:8px;">${escapeHtml(best || 'No metrics yet')}</div>
          <div class="small muted" style="margin-top:4px;">${escapeHtml(formatDate(run.started_at || run.last_artifact_at) || 'Time unknown')} · ${escapeHtml(run.elapsed_label || 'elapsed unknown')}</div>
        </button>`;
      }).join('');
      els.runList.querySelectorAll('.run-card').forEach(card => {
        card.addEventListener('click', () => selectRun(card.dataset.run).catch(showError));
      });
    }

    function syncFilterOptions() {
      const rows = currentAllRows();
      const metricNames = Array.from(new Set(rows.flatMap(row => Object.keys(row).filter(isMetricKey)))).sort(metricSort);
      if (!metricNames.includes(state.metric) || !hasNumericMetric(rows, state.metric)) {
        state.metric = preferredMetric(metricNames, rows);
      }
      const run = (state.detail && state.detail.run) || {};
      const sourceValues = ['all', ...unique([...(run.source_datasets || []), ...rows.map(row => row.source_dataset)])];
      const targetValues = ['all', ...unique([...(run.target_datasets || []), ...rows.map(row => row.target_dataset)])];
      if (!sourceValues.includes(state.source)) state.source = 'all';
      if (!targetValues.includes(state.target)) state.target = 'all';
      fillSelect(els.metricSelect, metricNames, state.metric, name => metricLabel(name));
      fillSelect(els.sourceSelect, sourceValues, state.source, value => value === 'all' ? 'All sources' : value);
      fillSelect(els.targetSelect, targetValues, state.target, value => value === 'all' ? 'All targets' : value);
      els.timeModeSelect.value = state.timeMode;
    }

    function renderDetail() {
      const detail = state.detail;
      if (!detail) return;
      const run = detail.run || {};
      els.experimentName.textContent = run.experiment_name || 'Benchmark Results';
      els.selectedRunId.textContent = run.run_id || '';
      setStatus(run.status || 'unknown');
      els.elapsedStat.textContent = run.elapsed_label || '--';
      els.runTimeStat.textContent = formatDateRange(run.started_at, run.finished_at) || formatDate(run.last_artifact_at) || '--';
      setBestStat('window', run.best && run.best.window);
      setBestStat('recording', run.best && run.best.recording);
      els.modelsStat.textContent = run.model_count ?? '--';
      els.datasetsStat.textContent = `${(run.source_datasets || []).length} source · ${(run.target_datasets || []).length} target`;
      els.windowingStat.textContent = run.window_seconds && run.stride_seconds ? `${run.window_seconds}s / ${run.stride_seconds}s` : '--';
      els.samplingStat.textContent = run.sample_rate_hz ? `${run.sample_rate_hz} Hz` : '--';
      els.metricRowsStat.textContent = run.metric_row_count ?? '0';
      els.artifactCountStat.textContent = `${run.artifact_count || 0} artifacts`;
      els.deleteRunButton.disabled = !run.run_id;
      renderMetricTable();
      renderDetailsPanel(run);
      renderArtifacts(detail.artifacts || []);
    }

    function renderEmptyRunDetail() {
      els.experimentName.textContent = 'Benchmark Results';
      els.selectedRunId.textContent = 'No run selected';
      setStatus('unknown');
      els.elapsedStat.textContent = '--';
      els.runTimeStat.textContent = '--';
      setBestStat('window', null);
      setBestStat('recording', null);
      els.modelsStat.textContent = '--';
      els.datasetsStat.textContent = '--';
      els.windowingStat.textContent = '--';
      els.samplingStat.textContent = '--';
      els.metricRowsStat.textContent = '0';
      els.artifactCountStat.textContent = '0 artifacts';
      els.deleteRunButton.disabled = true;
      els.chartArea.innerHTML = '<div class="empty">No run selected</div>';
      els.timeChartArea.innerHTML = '<div class="empty">No run selected</div>';
      els.distributionChartArea.innerHTML = '<div class="empty">No run selected</div>';
      els.metricTableBody.innerHTML = '<tr><td colspan="11">No run selected</td></tr>';
      els.runDetails.innerHTML = '';
      els.datasetChips.innerHTML = '';
      els.modelChips.innerHTML = '';
      els.artifactTableBody.innerHTML = '<tr><td colspan="4">No run selected</td></tr>';
    }

    function renderMetricTable() {
      const rows = filteredRows();
      els.selectedMetricHead.textContent = metricLabel(state.metric);
      if (!rows.length) {
        els.chartArea.innerHTML = '<div class="empty">No metric rows match these filters</div>';
        els.timeChartArea.innerHTML = '<div class="empty">No metric rows match these filters</div>';
        els.distributionChartArea.innerHTML = '<div class="empty">No distribution rows match these filters</div>';
        els.metricTableBody.innerHTML = '<tr><td colspan="11">No metric rows</td></tr>';
        return;
      }
      const sorted = [...rows].sort(compareSelectedMetric);
      renderBars(sorted);
      renderTimeScatter(sorted);
      renderDistribution(sorted);
      els.metricTableBody.innerHTML = sorted.map(row => `<tr>
        <td>${escapeHtml(row.model_id)}</td>
        <td>${escapeHtml(row.source_dataset)}</td>
        <td>${escapeHtml(row.target_dataset)}</td>
        <td class="metric-cell"><strong>${formatMetric(row[state.metric], state.metric)}</strong></td>
        <td class="metric-cell">${formatMetric(row.auroc, 'auroc')}</td>
        <td class="metric-cell">${formatMetric(row.average_precision, 'average_precision')}</td>
        <td class="metric-cell">${formatMetric(row.f1, 'f1')}</td>
        <td class="metric-cell">${formatMetric(row.accuracy, 'accuracy')}</td>
        <td class="metric-cell">${formatMetric(row.sensitivity, 'sensitivity')}</td>
        <td class="metric-cell">${formatMetric(row.specificity, 'specificity')}</td>
        <td class="metric-cell">${formatMetric(row.n, 'n')}</td>
      </tr>`).join('');
    }

    function renderBars(rows) {
      const numericRows = rows.filter(row => metricNumber(row[state.metric]) !== null);
      const unavailable = rows.length - numericRows.length;
      if (!numericRows.length) {
        els.chartArea.innerHTML = `<div class="empty">${escapeHtml(metricLabel(state.metric))} is unavailable for these ${escapeHtml(state.level)}-level rows</div>`;
        return;
      }
      const chartRows = numericRows.slice(0, 8);
      const maxValue = Math.max(...chartRows.map(row => metricNumber(row[state.metric]) || 0), 0.0001);
      const note = unavailable > 0
        ? `<div class="small muted">${unavailable} row${unavailable === 1 ? '' : 's'} have no ${escapeHtml(metricLabel(state.metric))} value for this evaluation level.</div>`
        : '';
      els.chartArea.innerHTML = chartRows.map(row => {
        const value = metricNumber(row[state.metric]) || 0;
        const width = COUNT_METRICS.has(state.metric) ? (value / maxValue) * 100 : Math.max(0, Math.min(100, value * 100));
        return `<div class="bar-row">
          <div>
            <div class="small"><strong>${escapeHtml(row.model_id)}</strong></div>
            <div class="bar-track" aria-hidden="true"><div class="bar-fill" style="width:${width}%"></div></div>
            <div class="small muted">${escapeHtml(row.source_dataset)} to ${escapeHtml(row.target_dataset)}</div>
          </div>
          <div class="metric-cell"><strong>${formatMetric(value, state.metric)}</strong></div>
        </div>`;
      }).join('') + note;
    }

    function renderTimeScatter(rows) {
      const xKey = state.timeMode === 'inference' ? 'inference_seconds' : 'train_evaluation_seconds';
      const xLabel = state.timeMode === 'inference' ? 'Prediction' : 'Training + evaluation';
      const metricName = metricLabel(state.metric);
      const points = rows.map(row => ({
        row,
        xValue: metricNumber(row[xKey]),
        yValue: metricNumber(row[state.metric])
      })).filter(point => point.xValue !== null && point.yValue !== null);
      if (!points.length) {
        const hasTiming = rows.some(row => metricNumber(row.train_evaluation_seconds) !== null || metricNumber(row.inference_seconds) !== null);
        const message = hasTiming
          ? `${xLabel} timing is unavailable for these rows`
          : 'Timing artifacts are not available for this run yet';
        els.timeChartArea.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
        return;
      }

      const width = 560;
      const height = 260;
      const margin = { top: 24, right: 18, bottom: 46, left: 52 };
      const plotWidth = width - margin.left - margin.right;
      const plotHeight = height - margin.top - margin.bottom;
      const maxX = Math.max(...points.map(point => point.xValue || 0), 0.001);
      const maxY = Math.max(...points.map(point => point.yValue || 0), 0.001);
      const xUpper = maxX * 1.08;
      const yUpper = COUNT_METRICS.has(state.metric) ? maxY * 1.08 : Math.max(1, maxY * 1.02);
      const xFor = value => margin.left + ((value || 0) / xUpper) * plotWidth;
      const yFor = value => margin.top + plotHeight - ((value || 0) / yUpper) * plotHeight;
      const xAxisY = margin.top + plotHeight;
      const yAxisX = margin.left;
      const xMaxLabel = formatDurationShort(xUpper);
      const yMaxLabel = formatMetric(yUpper, state.metric);

      const circles = points.map(point => {
        const row = point.row;
        const title = [
          `Model: ${row.model_id}`,
          `Source: ${row.source_dataset}`,
          `Target: ${row.target_dataset}`,
          `Level: ${row.evaluation_level}`,
          `${metricName}: ${formatMetric(point.yValue, state.metric)}`,
          `${xLabel}: ${formatDurationShort(point.xValue)}`,
          row.requires_training === false ? 'Training required: no' : null,
          row.train_seconds !== undefined ? `Training: ${formatDurationShort(row.train_seconds)}` : null,
          row.training_wall_seconds !== undefined ? `Training wall time: ${formatDurationShort(row.training_wall_seconds)}` : null,
          row.evaluation_seconds !== undefined ? `Evaluation: ${formatDurationShort(row.evaluation_seconds)}` : null,
          row.inference_seconds !== undefined ? `Prediction: ${formatDurationShort(row.inference_seconds)}` : null,
          row.input_kind ? `Input: ${row.input_kind}` : null,
          row.inference_row_count !== undefined ? `Prediction rows: ${formatMetric(row.inference_row_count, 'n')}` : null
        ].filter(Boolean).join('\n');
        return `<g>
          <title>${escapeHtml(title)}</title>
          <circle class="scatter-point" cx="${xFor(point.xValue).toFixed(2)}" cy="${yFor(point.yValue).toFixed(2)}" r="5.5"></circle>
        </g>`;
      }).join('');

      els.timeChartArea.innerHTML = `
        <svg class="scatter-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeAttr(metricName)} by ${escapeAttr(xLabel)}">
          <line class="scatter-grid-line" x1="${margin.left}" y1="${margin.top}" x2="${width - margin.right}" y2="${margin.top}"></line>
          <line class="scatter-grid-line" x1="${width - margin.right}" y1="${margin.top}" x2="${width - margin.right}" y2="${xAxisY}"></line>
          <line class="scatter-axis" x1="${yAxisX}" y1="${margin.top}" x2="${yAxisX}" y2="${xAxisY}"></line>
          <line class="scatter-axis" x1="${yAxisX}" y1="${xAxisY}" x2="${width - margin.right}" y2="${xAxisY}"></line>
          ${circles}
          <text class="scatter-label" x="${margin.left}" y="${height - 24}" text-anchor="middle">0s</text>
          <text class="scatter-label" x="${width - margin.right}" y="${height - 24}" text-anchor="end">${escapeHtml(xMaxLabel)}</text>
          <text class="scatter-label" x="${width / 2}" y="${height - 7}" text-anchor="middle">${escapeHtml(xLabel)} time</text>
          <text class="scatter-label" x="${margin.left - 8}" y="${xAxisY}" text-anchor="end">0</text>
          <text class="scatter-label" x="${margin.left - 8}" y="${margin.top + 4}" text-anchor="end">${escapeHtml(yMaxLabel)}</text>
          <text class="scatter-label" x="${margin.left}" y="14">${escapeHtml(metricName)}</text>
        </svg>`;
    }

    function renderDistribution(rows) {
      const rawRows = rows.map(row => {
        const n = metricNumber(row.n);
        let positive = metricNumber(row.positive);
        let negative = metricNumber(row.negative);
        if (n === null) return null;
        if (positive === null && negative !== null) positive = Math.max(0, n - negative);
        if (negative === null && positive !== null) negative = Math.max(0, n - positive);
        if (positive === null || negative === null) return null;
        return {
          row,
          n,
          positive,
          negative,
          fraction: n > 0 ? positive / n : null
        };
      }).filter(Boolean);
      const grouped = new Map();
      rawRows.forEach(item => {
        const key = [
          item.row.evaluation_level || '',
          item.row.target_dataset || '',
          item.n,
          item.positive,
          item.negative
        ].join('||');
        if (!grouped.has(key)) {
          grouped.set(key, {
            evaluationLevel: item.row.evaluation_level || state.level,
            targetDataset: item.row.target_dataset || '',
            n: item.n,
            positive: item.positive,
            negative: item.negative,
            fraction: item.fraction,
            sourceDatasets: new Set(),
            modelIds: new Set(),
            inputKinds: new Set(),
            rowCount: 0
          });
        }
        const group = grouped.get(key);
        group.rowCount += 1;
        if (item.row.source_dataset) group.sourceDatasets.add(item.row.source_dataset);
        if (item.row.model_id) group.modelIds.add(item.row.model_id);
        if (item.row.input_kind) group.inputKinds.add(item.row.input_kind);
      });
      const distributionRows = Array.from(grouped.values()).map(item => ({
        ...item,
        sourceDatasets: Array.from(item.sourceDatasets).sort(),
        modelIds: Array.from(item.modelIds).sort(),
        inputKinds: Array.from(item.inputKinds).sort()
      })).sort((left, right) => {
        const targetCompare = String(left.targetDataset).localeCompare(String(right.targetDataset));
        if (targetCompare !== 0) return targetCompare;
        return Number(right.n || 0) - Number(left.n || 0);
      });
      if (!distributionRows.length) {
        els.distributionChartArea.innerHTML = '<div class="empty">Label-count metrics are unavailable for these rows</div>';
        return;
      }
      const cohortCounts = new Map();
      distributionRows.forEach(item => {
        const key = `${item.evaluationLevel || ''}||${item.targetDataset || ''}`;
        cohortCounts.set(key, (cohortCounts.get(key) || 0) + 1);
      });
      const chartRows = distributionRows.slice(0, 10);
      els.distributionChartArea.innerHTML = chartRows.map(item => {
        const positiveWidth = item.n > 0 ? (item.positive / item.n) * 100 : 0;
        const negativeWidth = Math.max(0, 100 - positiveWidth);
        const cohortKey = `${item.evaluationLevel || ''}||${item.targetDataset || ''}`;
        const hasMultipleCohorts = (cohortCounts.get(cohortKey) || 0) > 1;
        const modelScope = item.modelIds.length === 1 ? item.modelIds[0] : `${item.modelIds.length} models`;
        const sourceScope = item.sourceDatasets.length === 1 ? item.sourceDatasets[0] : `${item.sourceDatasets.length} sources`;
        const inputScope = item.inputKinds.length ? ` · ${item.inputKinds.join(', ')}` : '';
        const cohortScope = hasMultipleCohorts ? ' · distinct evaluated cohort' : '';
        const title = [
          `Target: ${item.targetDataset}`,
          `Level: ${item.evaluationLevel}`,
          `Total: ${formatMetric(item.n, 'n')}`,
          `Positive: ${formatMetric(item.positive, 'n')} (${formatPercent(item.fraction)})`,
          `Negative: ${formatMetric(item.negative, 'n')}`,
          `Sources: ${item.sourceDatasets.join(', ') || '--'}`,
          `Models: ${item.modelIds.join(', ') || '--'}`,
          `Input kinds: ${item.inputKinds.join(', ') || '--'}`
        ].join('\n');
        return `<div class="distribution-row" title="${escapeAttr(title)}">
          <div>
            <div class="small"><strong>${escapeHtml(item.targetDataset)}</strong> · ${escapeHtml(item.evaluationLevel)}</div>
            <div class="distribution-track" aria-hidden="true">
              <div class="distribution-negative" style="width:${negativeWidth}%"></div>
              <div class="distribution-positive" style="width:${positiveWidth}%"></div>
            </div>
            <div class="small muted">${escapeHtml(sourceScope)} · ${escapeHtml(modelScope)}${escapeHtml(inputScope)}${escapeHtml(cohortScope)}</div>
          </div>
          <div class="metric-cell">
            <strong>${escapeHtml(formatPercent(item.fraction))}</strong>
            <div class="small muted">${escapeHtml(formatMetric(item.n, 'n'))} rows</div>
          </div>
        </div>`;
      }).join('');
    }

    function renderDetailsPanel(run) {
      els.runDetails.innerHTML = `
        <p><strong>Artifact root:</strong> <span class="muted">${escapeHtml(run.artifact_root || '--')}</span></p>
        <p style="margin-top:8px;"><strong>Random seed:</strong> ${escapeHtml(String(run.random_seed ?? '--'))}</p>
        <p style="margin-top:8px;"><strong>Recording aggregation:</strong> ${escapeHtml(run.recording_aggregation || '--')} / ${escapeHtml(run.recording_label_policy || '--')}</p>
      `;
      const datasets = [...(run.source_datasets || []), ...(run.target_datasets || [])];
      els.datasetChips.innerHTML = unique(datasets).map(item => `<span class="chip">${escapeHtml(item)}</span>`).join('');
      els.modelChips.innerHTML = (run.models || []).map(item => `<span class="chip">${escapeHtml(item)}</span>`).join('');
    }

    function renderArtifacts(artifacts) {
      if (!artifacts.length) {
        els.artifactTableBody.innerHTML = '<tr><td colspan="4">No artifacts found</td></tr>';
        return;
      }
      els.artifactTableBody.innerHTML = artifacts.map(item => `<tr>
        <td>${escapeHtml(item.label || '')}</td>
        <td class="small muted">${escapeHtml(item.uri || '')}</td>
        <td>${escapeHtml(formatDate(item.last_modified) || '--')}</td>
        <td>${escapeHtml(formatBytes(item.size))}</td>
      </tr>`).join('');
    }

    function renderCacheDatasetFilter(datasets) {
      const values = ['all', ...datasets];
      if (!values.includes(state.cacheDataset)) state.cacheDataset = 'all';
      fillSelect(els.cacheDatasetFilter, values, state.cacheDataset, value => value === 'all' ? 'All datasets' : value);
    }

    function renderCacheTable() {
      const entries = state.cacheEntries || [];
      els.cacheStatusPill.className = `pill ${entries.length ? 'succeeded' : 'unknown'}`;
      els.cacheStatusPill.textContent = entries.length ? `${entries.length} cached` : 'empty';
      if (!entries.length) {
        els.cacheTableBody.innerHTML = '<tr><td colspan="8">No cached partitions found</td></tr>';
        return;
      }
      els.cacheTableBody.innerHTML = entries.map((entry, index) => `<tr>
        <td>${escapeHtml(entry.kind_label || entry.kind || '')}</td>
        <td>${escapeHtml(entry.dataset || '')}</td>
        <td><code>${escapeHtml(entry.short_key || String(entry.cache_key || '').slice(0, 12))}</code></td>
        <td class="small">${escapeHtml(formatCacheSettings(entry))}</td>
        <td>${escapeHtml(`${entry.object_count || 0} · ${formatBytes(entry.size)}`)}</td>
        <td>${escapeHtml(formatDate(entry.last_modified) || '--')}</td>
        <td class="small muted">${escapeHtml(entry.uri || '')}</td>
        <td><button class="btn danger compact-action" type="button" data-delete-cache="${index}">Delete</button></td>
      </tr>`).join('');
      els.cacheTableBody.querySelectorAll('[data-delete-cache]').forEach(button => {
        button.addEventListener('click', () => deleteCacheEntry(Number(button.dataset.deleteCache)).catch(showCacheError));
      });
    }

    function renderExplorer() {
      const payload = state.explorer || {};
      const datasets = payload.datasets || [];
      const models = payload.models || [];
      els.explorerStatusPill.className = `pill ${datasets.length || models.length ? 'succeeded' : 'unknown'}`;
      els.explorerStatusPill.textContent = `${datasets.length} datasets · ${models.length} models`;
      renderDatasetExplorer(datasets);
      renderModelExplorer(models);
    }

    function renderDatasetExplorer(datasets) {
      const query = state.explorerDatasetQuery.trim().toLowerCase();
      const rows = datasets.filter(row => !query || JSON.stringify(row).toLowerCase().includes(query));
      els.datasetExplorerStat.textContent = datasets.length
        ? `${datasets.length} registered datasets · ${formatMetric(datasets.reduce((sum, row) => sum + Number(row.recordings || 0), 0), 'n')} recordings`
        : 'No catalog datasets loaded.';
      if (!rows.length) {
        els.datasetExplorerBody.innerHTML = '<tr><td colspan="7">No datasets match this filter</td></tr>';
        return;
      }
      els.datasetExplorerBody.innerHTML = rows.map(row => {
        const afFraction = formatPercent(row.af_present_fraction);
        const samplingRates = (row.sampling_rates || []).map(rate => `${rate} Hz`).join(', ') || '--';
        const leads = (row.lead_names || []).slice(0, 10).join(', ') || '--';
        const extraLeads = (row.lead_names || []).length > 10 ? ` +${(row.lead_names || []).length - 10}` : '';
        return `<tr>
          <td><strong>${escapeHtml(row.dataset || '')}</strong></td>
          <td class="metric-cell">${escapeHtml(formatMetric(row.recordings, 'n'))}</td>
          <td class="metric-cell">${escapeHtml(formatMetric(row.af_present, 'n'))} / ${escapeHtml(formatMetric(row.af_absent, 'n'))}<div class="small muted">${escapeHtml(afFraction)} positive</div></td>
          <td class="metric-cell">${escapeHtml(formatDurationLong(Number(row.total_duration_seconds || 0)))}</td>
          <td>${escapeHtml(samplingRates)}</td>
          <td>${escapeHtml(leads)}${escapeHtml(extraLeads)}</td>
          <td class="metric-cell">${escapeHtml(formatMetric(row.processed_recordings, 'n'))}<div class="small muted">${escapeHtml(formatMetric(row.annotation_recordings, 'n'))} annotated</div></td>
        </tr>`;
      }).join('');
    }

    function renderModelExplorer(models) {
      const query = state.explorerModelQuery.trim().toLowerCase();
      const rows = models.filter(row => !query || JSON.stringify(row).toLowerCase().includes(query));
      const trainable = models.filter(model => model.requires_training !== false).length;
      els.modelExplorerStat.textContent = models.length
        ? `${models.length} supported models · ${trainable} trainable · ${models.length - trainable} rule/fixed`
        : 'No model metadata loaded.';
      if (!rows.length) {
        els.modelExplorerBody.innerHTML = '<tr><td colspan="6">No models match this filter</td></tr>';
        return;
      }
      els.modelExplorerBody.innerHTML = rows.map(row => {
        const params = Object.keys(row.configurable_parameters || {}).join(', ') || '--';
        const columns = (row.required_columns || []).join(', ') || '--';
        return `<tr>
          <td><strong>${escapeHtml(row.model_id || '')}</strong></td>
          <td>${escapeHtml(row.input_kind || '--')}</td>
          <td>${row.requires_training === false ? 'No' : 'Yes'}</td>
          <td class="small">${escapeHtml(columns)}</td>
          <td class="small">${escapeHtml(params)}</td>
          <td class="small">${escapeHtml(row.description || '')}</td>
        </tr>`;
      }).join('');
    }

    function formatCacheSettings(entry) {
      if (entry.kind === 'features') {
        const pieces = [
          entry.sample_rate_hz ? `${entry.sample_rate_hz} Hz` : null,
          entry.window_seconds && entry.stride_seconds ? `${entry.window_seconds}s/${entry.stride_seconds}s` : null,
          entry.label_policy || null,
          entry.lead_policy || null,
          entry.labeled_windows !== undefined && entry.labeled_windows !== null ? `${formatMetric(entry.labeled_windows, 'n')} windows` : null,
          entry.source_recordings !== undefined && entry.source_recordings !== null ? `${formatMetric(entry.source_recordings, 'n')} recordings` : null
        ].filter(Boolean);
        return pieces.join(' · ') || '--';
      }
      const counts = entry.counts && typeof entry.counts === 'object'
        ? Object.entries(entry.counts).map(([key, value]) => `${key}:${value}`).join(' ')
        : '';
      const ratios = [
        entry.train_ratio !== undefined ? `train ${entry.train_ratio}` : null,
        entry.val_ratio !== undefined ? `val ${entry.val_ratio}` : null,
        entry.test_ratio !== undefined ? `test ${entry.test_ratio}` : null
      ].filter(Boolean).join(' / ');
      return [
        entry.random_seed !== undefined && entry.random_seed !== null ? `seed ${entry.random_seed}` : null,
        entry.stratify_by || null,
        ratios || null,
        counts || null
      ].filter(Boolean).join(' · ') || '--';
    }

    async function deleteCacheEntry(index) {
      const entry = state.cacheEntries[index];
      if (!entry) return;
      const ok = window.confirm(
        `Delete cached ${entry.kind_label || entry.kind} for ${entry.dataset}?\n\n${entry.uri}\n\nRuns that reference this cache entry may not be readable after deletion.`
      );
      if (!ok) return;
      const response = await fetch('/api/cache', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: entry.kind,
          dataset: entry.dataset,
          cache_key: entry.cache_key
        })
      });
      const payload = await response.json();
      if (!response.ok || payload.ok === false) {
        const message = (payload.errors || [payload.error || 'Could not delete cache entry']).join('\n');
        throw new Error(message);
      }
      await loadCache();
    }

    function currentAllRows() {
      if (!state.detail) return [];
      const comparisons = state.detail.comparisons || {};
      return comparisons[state.level] || [];
    }

    function filteredRows() {
      const modelQuery = state.modelQuery.trim().toLowerCase();
      return currentAllRows().filter(row => {
        if (state.source !== 'all' && row.source_dataset !== state.source) return false;
        if (state.target !== 'all' && row.target_dataset !== state.target) return false;
        if (modelQuery && !String(row.model_id || '').toLowerCase().includes(modelQuery)) return false;
        return true;
      });
    }

    function activeJob() {
      return state.jobs.find(job => job.status === 'running') || null;
    }

    function jobStatusClass(status) {
      if (status === 'succeeded' || status === 'reused') return 'succeeded';
      if (status === 'failed' || status === 'cancelled') return 'failed';
      if (status === 'running' || status === 'queued' || status === 'incomplete') return 'incomplete';
      return 'unknown';
    }

    function setStatus(status) {
      els.statusPill.className = `pill ${jobStatusClass(status)}`;
      els.statusPill.textContent = status;
    }

    function setBestStat(level, best) {
      const stat = level === 'window' ? els.windowBestStat : els.recordingBestStat;
      const detail = level === 'window' ? els.windowBestDetail : els.recordingBestDetail;
      stat.textContent = best ? formatMetric(best.metric_value, best.metric_name) : '--';
      detail.textContent = best ? `${metricLabel(best.metric_name)} · ${best.model_id}` : '--';
    }

    function formatBest(best, compact = false) {
      if (!best) return '';
      const label = `${metricLabel(best.metric_name)} ${formatMetric(best.metric_value, best.metric_name)}`;
      return compact ? label : `${label} · ${best.model_id}`;
    }

    function fillSelect(select, values, selected, labeler) {
      select.innerHTML = values.map(value => `<option value="${escapeAttr(value)}"${value === selected ? ' selected' : ''}>${escapeHtml(labeler(value))}</option>`).join('');
    }

    function unique(values) {
      return Array.from(new Set(values.filter(Boolean))).sort();
    }

    function uniquePreserve(values) {
      const seen = new Set();
      const result = [];
      values.forEach(value => {
        const text = String(value || '').trim();
        if (!text || seen.has(text)) return;
        seen.add(text);
        result.push(text);
      });
      return result;
    }

    function splitList(value) {
      return String(value || '')
        .split(/[\n,]+/)
        .map(item => item.trim())
        .filter(Boolean);
    }

    function deepClone(value) {
      return JSON.parse(JSON.stringify(value || {}));
    }

    function numberValue(value) {
      const number = Number(value);
      return Number.isFinite(number) ? number : 0;
    }

    function parseScalar(value) {
      if (value === 'true') return true;
      if (value === 'false') return false;
      if (value === 'null') return null;
      if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
      try {
        const parsed = JSON.parse(value);
        if (parsed === null || typeof parsed !== 'object') return parsed;
      } catch (_) {
        // Fall through to string.
      }
      return value;
    }

    function sanitizeClientFilename(value) {
      const cleaned = String(value || '').trim().replace(/[^A-Za-z0-9_.=-]+/g, '-').replace(/^[-._]+|[-._]+$/g, '');
      return cleaned || 'benchmark-config';
    }

    function toSimpleYaml(value, indent = 0) {
      const pad = ' '.repeat(indent);
      if (Array.isArray(value)) {
        if (!value.length) return '[]';
        return value.map(item => {
          if (item && typeof item === 'object') {
            const rendered = toSimpleYaml(item, indent + 2);
            return `${pad}- ${rendered.startsWith(' ') ? rendered.trimStart() : rendered}`.replace(/\n/g, `\n${pad}  `);
          }
          return `${pad}- ${formatYamlScalar(item)}`;
        }).join('\n');
      }
      if (value && typeof value === 'object') {
        return Object.entries(value).map(([key, item]) => {
          if (Array.isArray(item) || (item && typeof item === 'object')) {
            const rendered = toSimpleYaml(item, indent + 2);
            return `${pad}${key}: ${rendered === '[]' ? '[]' : `\n${rendered}`}`;
          }
          return `${pad}${key}: ${formatYamlScalar(item)}`;
        }).join('\n');
      }
      return formatYamlScalar(value);
    }

    function formatYamlScalar(value) {
      if (value === null || value === undefined) return 'null';
      if (typeof value === 'number' || typeof value === 'boolean') return String(value);
      const text = String(value);
      return /^[A-Za-z0-9_.=/*:[\]-]+$/.test(text) ? text : JSON.stringify(text);
    }

    function metricSort(a, b) {
      const order = ['auroc', 'average_precision', 'f1', 'accuracy', 'sensitivity', 'specificity', 'precision', 'recall', 'npv', 'balanced_accuracy', 'n', 'positive', 'negative'];
      return (order.indexOf(a) === -1 ? 999 : order.indexOf(a)) - (order.indexOf(b) === -1 ? 999 : order.indexOf(b)) || a.localeCompare(b);
    }

    const NON_METRIC_KEYS = new Set([
      'evaluation_level',
      'source_dataset',
      'target_dataset',
      'model_id',
      'input_kind',
      'train_seconds',
      'training_row_count',
      'validation_row_count',
      'requires_training',
      'training_wall_seconds',
      'evaluation_seconds',
      'evaluation_row_count',
      'inference_seconds',
      'inference_row_count',
      'train_evaluation_seconds'
    ]);
    const COUNT_METRICS = new Set(['n', 'positive', 'negative']);
    const METRIC_PRIORITY = ['auroc', 'average_precision', 'f1', 'accuracy', 'sensitivity', 'specificity', 'precision', 'recall', 'npv', 'balanced_accuracy', 'n', 'positive', 'negative'];

    function isMetricKey(key) {
      return !NON_METRIC_KEYS.has(key);
    }

    function metricLabel(metric) {
      const labels = {
        auroc: 'AUROC',
        average_precision: 'Avg precision',
        f1: 'F1',
        accuracy: 'Accuracy',
        sensitivity: 'Sensitivity',
        specificity: 'Specificity',
        precision: 'Precision',
        recall: 'Recall',
        npv: 'NPV',
        balanced_accuracy: 'Balanced accuracy',
        n: 'n',
        positive: 'Positive',
        negative: 'Negative'
      };
      return labels[metric] || metric;
    }

    function formatMetric(value, metric) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return '--';
      const number = Number(value);
      if (COUNT_METRICS.has(metric)) return fmtNumber.format(number);
      return fmtNumber.format(number);
    }

    function formatPercent(value) {
      const number = metricNumber(value);
      if (number === null) return '--';
      return `${trimFixed(number * 100, number < 0.1 ? 2 : 1)}%`;
    }

    function formatDurationShort(value) {
      const seconds = metricNumber(value);
      if (seconds === null) return '--';
      if (seconds === 0) return '0s';
      if (seconds < 1) return `${trimFixed(seconds, 3)}s`;
      if (seconds < 60) return `${trimFixed(seconds, seconds < 10 ? 2 : 1)}s`;
      let rounded = Math.round(seconds);
      const days = Math.floor(rounded / 86400);
      rounded -= days * 86400;
      const hours = Math.floor(rounded / 3600);
      rounded -= hours * 3600;
      const minutes = Math.floor(rounded / 60);
      const secs = rounded - minutes * 60;
      if (days > 0) return `${days}d ${hours}h ${minutes}m`;
      if (hours > 0) return `${hours}h ${minutes}m ${String(secs).padStart(2, '0')}s`;
      return `${minutes}m ${String(secs).padStart(2, '0')}s`;
    }

    function formatDurationLong(value) {
      const seconds = metricNumber(value);
      if (seconds === null) return '--';
      if (seconds < 3600) return formatDurationShort(seconds);
      const hours = seconds / 3600;
      if (hours < 24) return `${trimFixed(hours, 1)}h`;
      return `${trimFixed(hours / 24, 1)}d`;
    }

    function trimFixed(value, digits) {
      return Number(value).toFixed(digits).replace(/\.?0+$/, '');
    }

    function metricNumber(value) {
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    }

    function compareSelectedMetric(a, b) {
      const av = metricNumber(a[state.metric]);
      const bv = metricNumber(b[state.metric]);
      if (av === null && bv === null) return String(a.model_id).localeCompare(String(b.model_id));
      if (av === null) return 1;
      if (bv === null) return -1;
      return bv - av || String(a.model_id).localeCompare(String(b.model_id));
    }

    function hasNumericMetric(rows, metric) {
      return rows.some(row => metricNumber(row[metric]) !== null);
    }

    function preferredMetric(metricNames, rows) {
      for (const metric of METRIC_PRIORITY) {
        if (metricNames.includes(metric) && hasNumericMetric(rows, metric)) return metric;
      }
      return metricNames[0] || 'auroc';
    }

    function formatDate(value) {
      if (!value) return null;
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? null : fmtDate.format(date);
    }

    function formatDateRange(start, finish) {
      const left = formatDate(start);
      const right = formatDate(finish);
      if (left && right) return `${left} to ${right}`;
      return left || right || null;
    }

    function formatBytes(value) {
      if (!value) return '--';
      const units = ['B', 'KB', 'MB', 'GB'];
      let size = Number(value);
      let unit = 0;
      while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
      }
      return `${fmtNumber.format(size)} ${units[unit]}`;
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[char]));
    }

    function escapeAttr(value) {
      return escapeHtml(value);
    }

    function showError(error) {
      els.runErrors.innerHTML = `<div class="error">${escapeHtml(error.message || error)}</div>`;
    }

    function showCacheError(error) {
      els.cacheErrors.innerHTML = `<div class="error">${escapeHtml(error.message || error)}</div>`;
    }

    function showRunnerError(error) {
      els.runnerErrors.innerHTML = `<div class="error">${escapeHtml(error.message || error)}</div>`;
    }

    function showExplorerError(error) {
      els.explorerErrors.innerHTML = `<div class="error">${escapeHtml(error.message || error)}</div>`;
    }

    function showBuilderError(error) {
      renderValidation({ ok: false, errors: [error.message || String(error)], yaml: els.yamlPreview.textContent });
    }

    function activateView(viewId) {
      els.viewTabs.forEach(item => item.classList.toggle('active', item.dataset.view === viewId));
      els.sidebarPanels.forEach(panel => panel.classList.toggle('active', panel.dataset.sidebarPanel === viewId));
      document.querySelectorAll('.view').forEach(view => view.classList.toggle('active', view.id === viewId));
    }

    function refreshView(viewId) {
      if (viewId === 'resultsView') return loadRuns().catch(showError);
      if (viewId === 'explorerView') return loadExplorer().catch(showExplorerError);
      if (viewId === 'runnerView') return loadJobs().catch(showRunnerError);
      if (viewId === 'cacheView') return loadCache().catch(showCacheError);
      return Promise.resolve();
    }

    els.viewTabs.forEach(button => {
      button.addEventListener('click', () => {
        activateView(button.dataset.view);
        refreshView(button.dataset.view);
      });
    });

    document.querySelectorAll('.segmented button').forEach(button => {
      button.addEventListener('click', () => {
        state.level = button.dataset.level;
        document.querySelectorAll('.segmented button').forEach(item => item.classList.toggle('active', item === button));
        syncFilterOptions();
        renderMetricTable();
      });
    });

    els.metricSelect.addEventListener('change', () => {
      state.metric = els.metricSelect.value;
      renderMetricTable();
    });
    els.timeModeSelect.addEventListener('change', () => {
      state.timeMode = els.timeModeSelect.value;
      renderMetricTable();
    });
    els.sourceSelect.addEventListener('change', () => {
      state.source = els.sourceSelect.value;
      renderMetricTable();
    });
    els.targetSelect.addEventListener('change', () => {
      state.target = els.targetSelect.value;
      renderMetricTable();
    });
    els.modelSearch.addEventListener('input', () => {
      state.modelQuery = els.modelSearch.value;
      renderMetricTable();
    });
    els.runSearch.addEventListener('input', () => {
      state.query = els.runSearch.value;
      renderRunList();
    });
    els.refreshButton.addEventListener('click', () => loadRuns().catch(showError));
    els.deleteRunButton.addEventListener('click', () => deleteSelectedRun().catch(showError));
    els.refreshExplorerButton.addEventListener('click', () => loadExplorer().catch(showExplorerError));
    els.datasetExplorerSearch.addEventListener('input', () => {
      state.explorerDatasetQuery = els.datasetExplorerSearch.value;
      if (state.explorer) renderDatasetExplorer(state.explorer.datasets || []);
    });
    els.modelExplorerSearch.addEventListener('input', () => {
      state.explorerModelQuery = els.modelExplorerSearch.value;
      if (state.explorer) renderModelExplorer(state.explorer.models || []);
    });
    els.refreshJobsButton.addEventListener('click', () => loadJobs().catch(showRunnerError));
    els.runnerSourceSelect.addEventListener('change', () => {
      syncRunnerSourceFields();
      state.runnerConfig = null;
      els.startRunnerJobButton.disabled = true;
      els.runnerValidationBox.className = 'validation';
      els.runnerValidationBox.textContent = 'No config loaded yet.';
    });
    els.runnerS3Select.addEventListener('change', () => {
      if (els.runnerS3Select.value !== 'manual') els.runnerS3KeyInput.value = els.runnerS3Select.value;
    });
    els.loadRunnerConfigButton.addEventListener('click', () => loadRunnerConfig().catch(showRunnerError));
    els.startRunnerJobButton.addEventListener('click', () => startRunnerJob().catch(showRunnerError));
    els.cancelRunnerJobButton.addEventListener('click', () => cancelActiveJob().catch(showRunnerError));
    els.refreshCacheButton.addEventListener('click', () => loadCache().catch(showCacheError));
    els.cacheDatasetFilter.addEventListener('change', () => {
      state.cacheDataset = els.cacheDatasetFilter.value;
      loadCache().catch(showCacheError);
    });

    els.presetSelect.addEventListener('change', () => applyBuilderPreset(els.presetSelect.value));
    [
      els.experimentNameInput,
      els.experimentDescriptionInput,
      els.randomSeedInput,
      els.sparkAppInput,
      els.sampleRateInput,
      els.windowSecondsInput,
      els.strideSecondsInput,
      els.trainRatioInput,
      els.valRatioInput,
      els.testRatioInput,
      els.recordingThresholdInput,
      els.filenameInput
    ].forEach(input => input.addEventListener('input', renderBuilderDraft));
    [
      els.stratifySelect,
      els.labelPolicySelect,
      els.recordingAggregationSelect,
      els.recordingLabelPolicySelect
    ].forEach(input => input.addEventListener('change', renderBuilderDraft));
    els.validateConfigButton.addEventListener('click', () => validateBuilderConfig().catch(showBuilderError));
    els.downloadConfigButton.addEventListener('click', () => downloadBuilderConfig().catch(showBuilderError));
    els.saveConfigS3Button.addEventListener('click', () => saveBuilderConfigToS3().catch(showBuilderError));
    els.addSourceDatasetButton.addEventListener('click', () => addManualDataset('source'));
    els.addTargetDatasetButton.addEventListener('click', () => addManualDataset('target'));
    els.addPreferredLeadButton.addEventListener('click', addManualPreferredLead);
    document.addEventListener('click', event => {
      document.querySelectorAll('.multi-select.open').forEach(container => {
        if (!container.contains(event.target)) closeMultiSelect(container);
      });
    });
    [
      [els.sourceDatasetManualInput, () => addManualDataset('source')],
      [els.targetDatasetManualInput, () => addManualDataset('target')],
      [els.preferredLeadManualInput, addManualPreferredLead]
    ].forEach(([input, action]) => {
      input.addEventListener('keydown', event => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        action();
      });
    });

    loadRuns().catch(showError);
    loadRunnerOptions().catch(showRunnerError);
    loadJobs().catch(showRunnerError);
    loadCache().catch(showCacheError);
    loadExplorer().catch(showExplorerError);
    loadBuilderOptions().catch(showBuilderError);
    window.setInterval(() => {
      const hasLiveJobs = state.jobs.some(job => job.status === 'running' || job.status === 'queued');
      if (hasLiveJobs || document.getElementById('runnerView').classList.contains('active')) {
        loadJobs().catch(showRunnerError);
      }
    }, 2000);
