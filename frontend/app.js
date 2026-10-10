/**
 * Sketch-to-Space - Interactive Frontend Application
 * Vanilla JS - Zero build tools - Zero external CDN dependencies
 */

(function () {
  "use strict";

  // Application State
  const state = {
    samples: [],
    currentPlanId: "synth_008",
    currentPlanData: null,
    activeTab: "viewPhoto",
    showBoxes: true,
    zoom: 1.0,
    tourStep: 0,
    tourActive: false,
    hoveredSuspectIds: new Set(),
  };

  // DOM Elements
  const howItWorksBtn = document.getElementById("howItWorksBtn");
  const guidedDemoBtn = document.getElementById("guidedDemoBtn");
  const howItWorksModal = document.getElementById("howItWorksModal");
  const closeHowItWorksBtn = document.getElementById("closeHowItWorksBtn");
  const closeHowItWorksFooterBtn = document.getElementById("closeHowItWorksFooterBtn");

  const uploadOwnBtn = document.getElementById("uploadOwnBtn");
  const uploadModal = document.getElementById("uploadModal");
  const closeUploadModalBtn = document.getElementById("closeUploadModalBtn");
  const cancelUploadBtn = document.getElementById("cancelUploadBtn");
  const submitUploadBtn = document.getElementById("submitUploadBtn");
  const dropZone = document.getElementById("dropZone");
  const fileInput = document.getElementById("fileInput");
  const uploadStatusBox = document.getElementById("uploadStatusBox");
  const uploadStatusTitle = document.getElementById("uploadStatusTitle");
  const uploadStatusDesc = document.getElementById("uploadStatusDesc");

  const samplesGrid = document.getElementById("samplesGrid");

  const tabPhoto = document.getElementById("tabPhoto");
  const tabCorrected = document.getElementById("tabCorrected");
  const tabSideBySide = document.getElementById("tabSideBySide");
  const viewPhoto = document.getElementById("viewPhoto");
  const viewCorrected = document.getElementById("viewCorrected");
  const viewSideBySide = document.getElementById("viewSideBySide");

  const viewportTransform = document.getElementById("viewportTransform");
  const planImage = document.getElementById("planImage");
  const boxesOverlay = document.getElementById("boxesOverlay");
  const boxHoverChip = document.getElementById("boxHoverChip");
  const sbsPlanImage = document.getElementById("sbsPlanImage");
  const svgContainer = document.getElementById("svgContainer");
  const sbsSvgContainer = document.getElementById("sbsSvgContainer");

  const statLabelsRead = document.getElementById("statLabelsRead");
  const statCheckable = document.getElementById("statCheckable");
  const statConflicts = document.getElementById("statConflicts");

  const actionBanner = document.getElementById("actionBanner");
  const bannerText = document.getElementById("bannerText");
  const undoBtn = document.getElementById("undoBtn");

  const unitAmbigPanel = document.getElementById("unitAmbigPanel");
  const unitAmbigList = document.getElementById("unitAmbigList");

  const conflictsSection = document.getElementById("conflictsSection");
  const conflictsContainer = document.getElementById("conflictsContainer");
  const cleanStateCard = document.getElementById("cleanStateCard");
  const cleanCoverageText = document.getElementById("cleanCoverageText");

  const envSumRooms = document.getElementById("envSumRooms");
  const envStated = document.getElementById("envStated");
  const envStatedRatio = document.getElementById("envStatedRatio");
  const envChainSum = document.getElementById("envChainSum");
  const envChainRatio = document.getElementById("envChainRatio");
  const envelopeFlagBanner = document.getElementById("envelopeFlagBanner");
  const roomsTableBody = document.getElementById("roomsTableBody");

  const coverageBarText = document.getElementById("coverageBarText");
  const coveragePercentText = document.getElementById("coveragePercentText");
  const coverageFill = document.getElementById("coverageFill");
  const unverifiedSummaryText = document.getElementById("unverifiedSummaryText");
  const unverifiedList = document.getElementById("unverifiedList");

  const tourModal = document.getElementById("tourModal");
  const closeTourBtn = document.getElementById("closeTourBtn");
  const tourStepBadge = document.getElementById("tourStepBadge");
  const tourTitle = document.getElementById("tourTitle");
  const tourText = document.getElementById("tourText");
  const tourBackBtn = document.getElementById("tourBackBtn");
  const tourSkipBtn = document.getElementById("tourSkipBtn");
  const tourNextBtn = document.getElementById("tourNextBtn");

  // =========================================================================
  // Samples Loading & Selection
  // =========================================================================
  async function loadSamples() {
    try {
      const resp = await fetch("/api/samples");
      if (!resp.ok) throw new Error("Failed to load sample plans");
      state.samples = await resp.json();
      renderSamplesGrid();
      if (state.samples.length > 0) {
        selectPlan(state.currentPlanId || state.samples[0].id);
      }
    } catch (err) {
      console.error("Error loading samples:", err);
      samplesGrid.innerHTML = `<div class="error-banner">Could not connect to backend server. Make sure uvicorn is running on port 8000.</div>`;
    }
  }

  function renderSamplesGrid() {
    samplesGrid.innerHTML = "";
    state.samples.forEach((sample) => {
      const card = document.createElement("div");
      const isActive = sample.id === state.currentPlanId;
      card.className = `sample-card ${isActive ? "active" : ""}`;
      card.setAttribute("role", "radio");
      card.setAttribute("aria-checked", isActive ? "true" : "false");
      card.setAttribute("tabindex", "0");
      card.dataset.id = sample.id;

      const kindClass = sample.kind === "synthetic" ? "badge-synth" : "badge-real";
      const chipClass = sample.status_chip === "conflict found" ? "chip-conflict" : (sample.status_chip === "clean" ? "chip-clean" : "chip-neutral");

      card.innerHTML = `
        <div class="sample-thumb-wrapper">
          <img src="${sample.thumbnail_url}" alt="${sample.title}" class="sample-thumb" loading="lazy">
        </div>
        <div class="sample-card-body">
          <div class="sample-card-meta">
            <span class="badge ${kindClass}">${sample.kind === "synthetic" ? "Synthetic" : "Real sketch"}</span>
            <span class="chip ${chipClass}">${sample.status_chip}</span>
            ${isActive ? `<span class="badge badge-active-pill"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg> Active Plan</span>` : ""}
          </div>
          <h3 class="sample-card-title">${escapeHtml(sample.title)}</h3>
          <p class="sample-card-desc">${escapeHtml(sample.description)}</p>
        </div>
      `;

      card.addEventListener("click", () => selectPlan(sample.id));
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectPlan(sample.id);
        }
      });
      samplesGrid.appendChild(card);
    });
  }

  async function selectPlan(planId) {
    state.currentPlanId = planId;
    actionBanner.hidden = true;

    renderSamplesGrid();
    await fetchAndRenderPlan(planId);
  }

  // =========================================================================
  // Plan Rendering & Workspace Population
  // =========================================================================
  async function fetchAndRenderPlan(planId) {
    try {
      const resp = await fetch(`/api/plan/${planId}`);
      if (!resp.ok) throw new Error(`Plan fetch failed: ${resp.status}`);
      state.currentPlanData = await resp.json();
      renderWorkspace(state.currentPlanData);
    } catch (err) {
      console.error("Error fetching plan data:", err);
    }
  }

  function renderWorkspace(data) {
    // 1. Stats Bar
    statLabelsRead.textContent = data.coverage.total_labels;
    statCheckable.textContent = `${data.coverage.labels_in_constraints} of ${data.coverage.total_labels}`;
    statConflicts.textContent = data.conflicts.length;

    // 2. Visual Inspector Tab - Photo
    planImage.src = data.image_url;
    sbsPlanImage.src = data.image_url;

    planImage.onload = () => {
      renderBoxOverlay(data.labels, planImage.naturalWidth, planImage.naturalHeight);
    };
    if (planImage.complete && planImage.naturalWidth) {
      renderBoxOverlay(data.labels, planImage.naturalWidth, planImage.naturalHeight);
    }

    // 3. Visual Inspector Tab - Corrected SVG
    loadCorrectedSvg(data.id);

    // 4. Unit Ambiguity Warnings Panel
    if (data.unit_ambiguous && data.unit_ambiguous.length > 0) {
      unitAmbigPanel.hidden = false;
      unitAmbigList.innerHTML = "";
      data.unit_ambiguous.forEach((u) => {
        const item = document.createElement("div");
        item.className = "unit-warning-item";
        item.innerHTML = `
          <p class="unit-warning-prompt">${escapeHtml(u.prompt)}</p>
          <div class="unit-choice-buttons">
            <button class="btn btn-sm btn-outline btn-unit-choice" data-id="${u.id}" data-text="${u.reading_1}">${escapeHtml(u.reading_1_label)}</button>
            <button class="btn btn-sm btn-outline btn-unit-choice" data-id="${u.id}" data-text="${u.reading_2}">${escapeHtml(u.reading_2_label)}</button>
          </div>
        `;
        item.querySelectorAll(".btn-unit-choice").forEach((btn) => {
          btn.addEventListener("click", () => {
            confirmEdit(btn.dataset.id, btn.dataset.text);
          });
        });
        unitAmbigList.appendChild(item);
      });
    } else {
      unitAmbigPanel.hidden = true;
    }

    // 5. Conflicts Section
    if (data.conflicts && data.conflicts.length > 0) {
      conflictsSection.hidden = false;
      cleanStateCard.hidden = true;
      renderConflicts(data.conflicts);
    } else {
      conflictsSection.hidden = true;
      cleanStateCard.hidden = false;
      cleanCoverageText.textContent = `${data.coverage.labels_in_constraints} of ${data.coverage.total_labels} labels verified across all checkable chains.`;
    }

    // 6. Areas Card
    envSumRooms.textContent = `${data.areas.sum_of_rooms_read_sq_ft} sq ft`;
    envStated.textContent = `${data.areas.stated_envelope_sq_ft} sq ft`;
    envStatedRatio.textContent = typeof data.areas.stated_envelope_ratio_pct === "number" ? `(${data.areas.stated_envelope_ratio_pct}%)` : "";
    envChainSum.textContent = `${data.areas.chain_sum_envelope_sq_ft} sq ft`;
    envChainRatio.textContent = typeof data.areas.chain_sum_envelope_ratio_pct === "number" ? `(${data.areas.chain_sum_envelope_ratio_pct}%)` : "";

    if (data.areas.envelope_flag && data.areas.envelope_flag.includes(">100%")) {
      envelopeFlagBanner.hidden = false;
      envelopeFlagBanner.textContent = `Warning: ${data.areas.envelope_flag}. Stated overall envelope is smaller than total interior room area.`;
    } else {
      envelopeFlagBanner.hidden = true;
    }

    // Rooms table
    roomsTableBody.innerHTML = "";
    if (data.areas.rooms && data.areas.rooms.length > 0) {
      data.areas.rooms.forEach((rm) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${escapeHtml(rm.room_name)}</strong></td>
          <td><code>${escapeHtml(rm.dimensions_formatted || rm.text_as_written)}</code></td>
          <td>${rm.area_sq_ft ? rm.area_sq_ft.toFixed(1) : "-"}</td>
          <td>${rm.area_sq_m ? rm.area_sq_m.toFixed(1) : "-"}</td>
        `;
        roomsTableBody.appendChild(tr);
      });
    } else {
      roomsTableBody.innerHTML = `<tr><td colspan="4" class="dim">No separate room sizes read.</td></tr>`;
    }

    // 7. Coverage Card
    const pct = data.coverage.total_labels > 0 ? Math.round((data.coverage.labels_in_constraints / data.coverage.total_labels) * 100) : 0;
    coverageBarText.textContent = `${data.coverage.labels_in_constraints} of ${data.coverage.total_labels} labels checkable`;
    coveragePercentText.textContent = `${pct}%`;
    coverageFill.style.width = `${pct}%`;

    const covBadge = document.getElementById("coverageHeaderBadge");
    if (covBadge) {
      if (pct === 100) {
        covBadge.className = "badge badge-ok";
        covBadge.textContent = "100% Verified";
      } else {
        covBadge.className = "badge badge-accent";
        covBadge.textContent = `${pct}% Checkable`;
      }
    }

    unverifiedSummaryText.textContent = `Unverified labels (${data.unverified ? data.unverified.length : 0})`;
    unverifiedList.innerHTML = "";
    if (data.unverified && data.unverified.length > 0) {
      data.unverified.forEach((u) => {
        const div = document.createElement("div");
        div.className = "unverified-item";
        div.innerHTML = `<code>${escapeHtml(u.text)}</code> (${u.applies_to || "label"}) - <span class="dim">${u.reason}</span>`;
        unverifiedList.appendChild(div);
      });
    }
  }

  // =========================================================================
  // SVG Bounding Box Overlays
  // =========================================================================
  function positionHoverChip(rectElem) {
    if (!boxHoverChip || !viewportTransform) return;
    const rBox = rectElem.getBoundingClientRect();
    const vBox = viewportTransform.getBoundingClientRect();
    const left = (rBox.left - vBox.left) + (rBox.width / 2);
    const top = (rBox.top - vBox.top) - 28;
    boxHoverChip.style.left = `${Math.max(10, left - 40)}px`;
    boxHoverChip.style.top = `${Math.max(4, top)}px`;
  }

  function renderBoxOverlay(labels, natW, natH) {
    if (!natW || !natH) return;
    boxesOverlay.setAttribute("viewBox", `0 0 ${natW} ${natH}`);
    boxesOverlay.innerHTML = "";

    if (!state.showBoxes) return;

    labels.forEach((lbl) => {
      if (!lbl.box_ok || !lbl.box) return;
      const [x0, y0, x1, y1] = lbl.box;
      const w = Math.max(1, x1 - x0);
      const h = Math.max(1, y1 - y0);

      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", x0);
      rect.setAttribute("y", y0);
      rect.setAttribute("width", w);
      rect.setAttribute("height", h);
      rect.dataset.id = lbl.id;

      let cls = "box-rect";
      if (lbl.status === "conflict_suspect") cls += " box-conflict";
      else if (lbl.status === "unit_ambiguous") cls += " box-ambig";
      if (state.hoveredSuspectIds.has(lbl.id)) cls += " box-pulse";

      rect.setAttribute("class", cls);

      // Native accessible title
      const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = `${lbl.text} (${lbl.status})\nApplies to: ${lbl.applies_to || "unbound"}`;
      rect.appendChild(title);

      // Interactive hover chip using --surface and --border outline
      rect.addEventListener("mouseenter", () => {
        if (!boxHoverChip) return;
        boxHoverChip.textContent = `${lbl.text} - ${lbl.status}`;
        boxHoverChip.hidden = false;
        positionHoverChip(rect);
      });
      rect.addEventListener("mousemove", () => {
        positionHoverChip(rect);
      });
      rect.addEventListener("mouseleave", () => {
        if (boxHoverChip) boxHoverChip.hidden = true;
      });

      boxesOverlay.appendChild(rect);
    });
  }

  // =========================================================================
  // Corrected SVG Loading
  // =========================================================================
  async function loadCorrectedSvg(planId) {
    try {
      const resp = await fetch(`/api/corrected/${planId}`);
      if (!resp.ok) throw new Error("Failed to load corrected SVG");
      const svgText = await resp.text();
      svgContainer.innerHTML = svgText;
      sbsSvgContainer.innerHTML = svgText;
    } catch (err) {
      console.error("Error loading corrected SVG:", err);
      svgContainer.innerHTML = `<div class="stage-placeholder">Could not render corrected plan.</div>`;
    }
  }

  // =========================================================================
  // Conflict Cards & Suggestions Rendering
  // =========================================================================
  function renderConflicts(conflicts) {
    conflictsContainer.innerHTML = "";

    conflicts.forEach((cf) => {
      const card = document.createElement("div");
      card.className = "conflict-card";

      // Mouse enter/leave to highlight suspect boxes
      card.addEventListener("mouseenter", () => {
        state.hoveredSuspectIds = new Set(cf.suspect_label_ids);
        updateBoxHighlights();
      });
      card.addEventListener("mouseleave", () => {
        state.hoveredSuspectIds = new Set();
        updateBoxHighlights();
      });

      // Header with plain-language constraint mismatch
      const header = document.createElement("div");
      header.className = "conflict-header";
      header.innerHTML = `
        <div class="conflict-header-top">
          <span class="conflict-badge-chip">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
            <span>Discrepancy Detected</span>
          </span>
          <div class="conflict-sentence">${escapeHtml(cf.sentence)}</div>
        </div>
        <div class="conflict-meta">
          <span class="conflict-metric-pill">Gap: <code>${cf.gap_formatted}</code></span>
          <span class="conflict-metric-pill">Excess: <code>${cf.excess_formatted}</code></span>
        </div>
      `;
      card.appendChild(header);

      // Ranked suggestions table
      const table = document.createElement("table");
      table.className = "suggestions-table";
      table.innerHTML = `
        <thead>
          <tr>
            <th>Ranked Fix</th>
            <th>Confidence</th>
            <th>Type</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody></tbody>
      `;
      const tbody = table.querySelector("tbody");

      if (cf.ranked_suggestions && cf.ranked_suggestions.length > 0) {
        cf.ranked_suggestions.forEach((sug) => {
          const tr = document.createElement("tr");
          const tagClass = sug.tag === "weak" ? "tag-weak" : (sug.tag === "partial" ? "tag-partial" : "tag-complete");
          const ocrText = sug.ocr_verified ? "OCR corroborated" : "ungrounded reading";

          tr.innerHTML = `
            <td>
              <div class="edit-change">
                <span class="val-old">${escapeHtml(sug.old_text)}</span>
                <span class="val-arrow">&rarr;</span>
                <span class="val-new">${escapeHtml(sug.new_text)}</span>
              </div>
              <span class="suggestion-note">${escapeHtml(sug.note || ocrText)}</span>
            </td>
            <td>
              <span class="dim">Cost: ${sug.cost}</span>
            </td>
            <td>
              <span class="tag-badge ${tagClass}">${sug.tag}</span>
            </td>
            <td>
              <button class="btn btn-sm btn-primary btn-accept" data-id="${sug.label_id}" data-new="${sug.new_text}">Accept</button>
            </td>
          `;

          const acceptBtn = tr.querySelector(".btn-accept");
          acceptBtn.addEventListener("click", () => {
            confirmEdit(sug.label_id, sug.new_text);
          });

          tbody.appendChild(tr);
        });
      } else {
        tbody.innerHTML = `<tr><td colspan="4" class="dim">No high-confidence correction candidates found.</td></tr>`;
      }

      card.appendChild(table);
      conflictsContainer.appendChild(card);
    });
  }

  function updateBoxHighlights() {
    document.querySelectorAll(".box-rect").forEach((rect) => {
      const id = rect.dataset.id;
      if (state.hoveredSuspectIds.has(id)) {
        rect.classList.add("box-pulse");
      } else {
        rect.classList.remove("box-pulse");
      }
    });
  }

  // =========================================================================
  // Confirm Edit & Undo via Real API
  // =========================================================================
  async function confirmEdit(labelId, newText) {
    try {
      const resp = await fetch("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: state.currentPlanId,
          label_id: labelId,
          new_text: newText,
        }),
      });

      if (!resp.ok) throw new Error("Confirm edit failed");
      const result = await resp.json();

      // Show Action Banner
      const ch = result.changed_labels && result.changed_labels[0];
      const diffStr = ch ? `${ch.old_text} &rarr; <strong>${ch.new_text}</strong>` : newText;
      const rem = result.remaining_conflicts ? result.remaining_conflicts.length : 0;
      const remMsg = rem === 0 ? "All constraints satisfied." : `${rem} conflict remaining.`;

      bannerText.innerHTML = `Accepted correction: ${diffStr}. ${remMsg}`;
      actionBanner.hidden = false;

      // Re-render workspace
      state.currentPlanData = result.plan;
      renderWorkspace(result.plan);
    } catch (err) {
      console.error("Error confirming edit:", err);
      alert("Failed to confirm edit on backend.");
    }
  }

  async function resetPlan() {
    try {
      const resp = await fetch("/api/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: state.currentPlanId }),
      });
      if (!resp.ok) throw new Error("Reset failed");
      const result = await resp.json();

      actionBanner.hidden = true;
      state.currentPlanData = result.plan;
      renderWorkspace(result.plan);
    } catch (err) {
      console.error("Error resetting plan:", err);
    }
  }

  undoBtn.addEventListener("click", resetPlan);

  // =========================================================================
  // Tabs Navigation
  // =========================================================================
  function setTab(tabId) {
    state.activeTab = tabId;
    [tabPhoto, tabCorrected, tabSideBySide].forEach((btn) => {
      const active = btn.getAttribute("aria-controls") === tabId;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });

    [viewPhoto, viewCorrected, viewSideBySide].forEach((pane) => {
      const visible = pane.id === tabId;
      pane.hidden = !visible;
      pane.classList.toggle("active", visible);
    });
  }

  tabPhoto.addEventListener("click", () => setTab("viewPhoto"));
  tabCorrected.addEventListener("click", () => setTab("viewCorrected"));
  tabSideBySide.addEventListener("click", () => setTab("viewSideBySide"));

  // =========================================================================
  // Upload Own Modal (501 Demonstration)
  // =========================================================================
  uploadOwnBtn.addEventListener("click", () => {
    uploadStatusBox.hidden = true;
    uploadModal.showModal();
  });
  closeUploadModalBtn.addEventListener("click", () => uploadModal.close());
  cancelUploadBtn.addEventListener("click", () => uploadModal.close());

  dropZone.addEventListener("click", () => fileInput.click());
  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    handleUploadAttempt();
  });
  fileInput.addEventListener("change", () => handleUploadAttempt());

  submitUploadBtn.addEventListener("click", () => handleUploadAttempt());

  async function handleUploadAttempt() {
    try {
      submitUploadBtn.disabled = true;
      const resp = await fetch("/api/upload", { method: "POST" });
      if (resp.status === 501) {
        const data = await resp.json();
        uploadStatusTitle.textContent = "Live reading is not wired in this build.";
        uploadStatusDesc.textContent = `${data.detail || "Pick a sample plan instead."}`;
        uploadStatusBox.hidden = false;
      }
    } catch (err) {
      uploadStatusTitle.textContent = "Live reading is not wired in this build.";
      uploadStatusDesc.textContent = "Pick a sample plan instead.";
      uploadStatusBox.hidden = false;
    } finally {
      submitUploadBtn.disabled = false;
    }
  }

  // =========================================================================
  // How It Works Modal
  // =========================================================================
  howItWorksBtn.addEventListener("click", () => howItWorksModal.showModal());
  closeHowItWorksBtn.addEventListener("click", () => howItWorksModal.close());
  closeHowItWorksFooterBtn.addEventListener("click", () => howItWorksModal.close());

  // =========================================================================
  // Guided Demo Interactive Tour
  // =========================================================================
  const tourSteps = [
    {
      step: 1,
      title: "Step 1: Perception & Dimension Mismatch",
      text: "Notice Synth 008 loaded on the left. The system transcribed all written dimensions into calibrated coordinate boxes. An injected room width discrepancy (4.129 m vs 4.729 m) is identified.",
      action: async () => {
        await selectPlan("synth_008");
        setTab("viewPhoto");
      },
    },
    {
      step: 2,
      title: "Step 2: Constraint Mismatch Caught",
      text: "On the right, see the Conflict Card. Code, not AI, verified the horizontal chain and detected that the inner dimension contradicts the boundary. Zero LLM arithmetic.",
      action: () => {
        conflictsSection.scrollIntoView({ behavior: "smooth" });
      },
    },
    {
      step: 3,
      title: "Step 3: Ranked Fix Candidates",
      text: "The deterministic solver ranks minimal corrections based on OCR corroboration and wall allowances. It proposes adjusting the contradictory dimension to 4.129 m.",
      action: () => {},
    },
    {
      step: 4,
      title: "Step 4: You Confirm the Fix",
      text: "Click 'Accept' on the top recommendation. Notice the diff appears, conflicts drop to 0, and the layout satisfies all checksums.",
      action: async () => {
        const acceptBtn = document.querySelector(".btn-accept");
        if (acceptBtn) {
          acceptBtn.click();
        }
      },
    },
    {
      step: 5,
      title: "Step 5: Unit Ambiguity Check (Test 6)",
      text: "Now switching to Test 6 - 2-Room Studio. In real sketches, notation like 7'x4\" and 7'x5\" bathroom labels can be ambiguous. The system flags them under Unit Ambiguity for human verification.",
      action: async () => {
        await selectPlan("test6");
        setTab("viewPhoto");
      },
    },
  ];

  guidedDemoBtn.addEventListener("click", () => {
    state.tourStep = 0;
    state.tourActive = true;
    showTourStep(0);
  });

  async function showTourStep(idx) {
    if (idx < 0 || idx >= tourSteps.length) {
      tourModal.close();
      state.tourActive = false;
      return;
    }
    state.tourStep = idx;
    const s = tourSteps[idx];
    tourStepBadge.textContent = `Step ${s.step} of ${tourSteps.length}`;
    tourTitle.textContent = s.title;
    tourText.textContent = s.text;

    tourBackBtn.disabled = idx === 0;
    tourNextBtn.textContent = idx === tourSteps.length - 1 ? "Finish Tour" : "Next";

    tourModal.showModal();
    if (s.action) await s.action();
  }

  tourNextBtn.addEventListener("click", () => showTourStep(state.tourStep + 1));
  tourBackBtn.addEventListener("click", () => showTourStep(state.tourStep - 1));
  tourSkipBtn.addEventListener("click", () => {
    tourModal.close();
    state.tourActive = false;
  });
  closeTourBtn.addEventListener("click", () => {
    tourModal.close();
    state.tourActive = false;
  });

  // =========================================================================
  // Helpers
  // =========================================================================
  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Initialize
  loadSamples();
})();
