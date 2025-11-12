import { supabase } from '../../env.js';
import { formatTimestamp } from '../../utils.js';

let allPredictions = [];
let verifiedTier;

export async function initializeHomePage(userTier) {
    verifiedTier = userTier;
    // Load predictions based on user tier
    await loadPredictions();
    // Populate per-league tab content from matches table
    await populateLeagueTabsFromMatches();
    // Initialize league tabs
    initializeLeagueTabs();
    // Initialize advanced filters
    initializeAdvancedFilters();
    // Initialize search/command input on the home page
    initializeSearchBar();
}

async function loadPredictions() {
    try {
        // Determine accessible tiers based on user subscription
        let accessibleTiers = ['free'];

        if (verifiedTier === 'Premium Tier') {
            accessibleTiers.push('premium');
        } else if (verifiedTier === 'VIP Tier') {
            accessibleTiers.push('premium', 'vip');
        } else if (verifiedTier === 'VVIP Tier') {
            accessibleTiers.push('premium', 'vip', 'vvip');
        }

        const { data: predictions, error } = await supabase
            .from('predictions')
            .select('*')
            .in('tier', accessibleTiers)
            .gte('kickoff_time', new Date().toISOString())
            .order('kickoff_time', { ascending: true })
            .limit(10);

        if (error) {
            console.warn('Error loading predictions:', error);
            return;
        }

        allPredictions = predictions || [];
        displayPredictions(allPredictions);
    } catch (error) {
        console.error('Error loading predictions:', error);
    }
}

function displayPredictions(predictions) {
    // Prefer the predictions container inside the currently active tab; fall back to the global id.
    const container = document.querySelector('.tab-content.active .predictions-container') || document.getElementById('predictions-container');
    if (!container) return;

    // if (predictions.length === 0) {
    //     container.innerHTML = `
    //         <div class="no-predictions">
    //             <h3>No predictions available</h3>
    //             <p>Check back later for new AI predictions!</p>
    //         </div>
    //     `;
    //     return;
    // }

    const predictionsHTML = predictions.map(prediction => `
        <div class="prediction-card tier-${prediction.tier}">
            <div class="match-header">
                <h4>${prediction.home_team} vs ${prediction.away_team}</h4>
                <span class="league">${prediction.league}</span>
            </div>
            <div class="prediction-content">
                <div class="prediction-result">
                    <span class="label">Prediction:</span>
                    <span class="result">${prediction.prediction}</span>
                </div>
                <div class="confidence">
                    <span class="label">Confidence:</span>
                    <span class="value">${prediction.confidence}%</span>
                </div>
                ${prediction.odds ? `
                    <div class="odds">
                        <span class="label">Odds:</span>
                        <span class="value">${prediction.odds}</span>
                    </div>
                ` : ''}
                <div class="kickoff">
                    <span class="label">Kickoff:</span>
                    <span class="time">${formatTimestamp(prediction.kickoff_time)}</span>
                </div>
            </div>
            ${prediction.reasoning ? `
                <div class="reasoning">
                    <p>${prediction.reasoning}</p>
                </div>
            ` : ''}
            <div class="prediction-actions">
                <button onclick="savePrediction('${prediction.id}')" class="btn-save">
                    Save to History
                </button>
            </div>
        </div>
    `).join('');

    container.innerHTML = predictionsHTML;
}

// League name mapping - maps various possible league names to their tab IDs
const LEAGUE_MAPPINGS = {
    // Premier League variations
    'premier league': 'premier-league',
    'epl': 'premier-league',
    'english premier league': 'premier-league',

    // La Liga variations
    'la liga': 'la-liga',
    'laliga': 'la-liga',
    'spanish primera division': 'la-liga',

    // Bundesliga variations
    'bundesliga': 'bundesliga',
    'german bundesliga': 'bundesliga',

    // Serie A variations
    'serie a': 'serie-a',
    'italian serie a': 'serie-a',

    // Ligue 1 variations
    'ligue 1': 'ligue1',
    'french ligue 1': 'ligue1',

    // Champions League variations
    'uefa champions league': 'champions-league',
    'champions league': 'champions-league',
    'ucl': 'champions-league',

    // Primeira Liga variations
    'primeira liga': 'primeira-liga',
    'portuguese primeira': 'primeira-liga',

    // Eredivisie variations
    'eredivisie': 'eredivisie',
    'dutch eredivisie': 'eredivisie',

    // Scottish Premiership variations
    'scottish premiership': 'scottish-premiership',
    'spfl': 'scottish-premiership',

    // Belgian Pro League variations
    'belgian pro league': 'belgian-pro-league',
    'jupiler pro league': 'belgian-pro-league',

    // Süper Lig variations
    'super lig': 'super-lig',
    'süper lig': 'super-lig',
    'turkish super lig': 'super-lig',

    // Brasileirão variations
    'brasileirao': 'brasileiro-serie-a',
    'brasileiro serie a': 'brasileiro-serie-a',
    'brasileirão série a': 'brasileiro-serie-a',

    // Liga Profesional variations
    'liga profesional': 'liga-profesional',
    'argentine primera division': 'liga-profesional',

    // MLS variations
    'mls': 'mls',
    'major league soccer': 'mls',

    // Liga MX variations
    'liga mx': 'liga-mx',
    'mexican primera': 'liga-mx',

    // Saudi Pro League variations
    'saudi pro league': 'saudi-pro-league',
    'saudi professional league': 'saudi-pro-league',

    // J1 League variations
    'j1': 'j1-league',
    'j1 league': 'j1-league',
    'japan j1 league': 'j1-league',

    // K League variations
    'k league': 'k-league',
    'k league 1': 'k-league',
    'korean k league': 'k-league',

    // Egyptian Premier variations
    'egyptian premier': 'egyptian-premier',
    'egyptian premier league': 'egyptian-premier',

    // South African Premier Division variations
    'south african premier': 'south-african-premier',
    'psl': 'south-african-premier',

    // Botola Pro variations
    'botola pro': 'botola-pro',
    'moroccan botola': 'botola-pro',

    // Copa Libertadores variations
    'copa libertadores': 'copa-libertadores',
    'conmebol libertadores': 'copa-libertadores',

    // Club World Cup variations
    'club world cup': 'club-world-cup',
    'fifa club world cup': 'club-world-cup',
};

// Populate per-league tab containers using the `matches` table from Supabase.
async function populateLeagueTabsFromMatches() {
    try {
        console.log('[populateLeagueTabsFromMatches] start');
        const nowIso = new Date().toISOString();
        console.log('[populateLeagueTabsFromMatches] querying matches with kickoff >=', nowIso);
        const { data: matches, error } = await supabase
            .from('matches')
            .select('*')
            .gte('kickoff_time', nowIso)
            .order('kickoff_time', { ascending: true })
            .limit(200);

        if (error) {
            console.warn('[populateLeagueTabsFromMatches] Error loading matches for tabs:', error);
            return;
        }

        if (!matches || matches.length === 0) {
            console.log('[populateLeagueTabsFromMatches] no upcoming matches returned');
            return;
        }

        console.log('[populateLeagueTabsFromMatches] matches fetched:', matches.length);

        // Clear existing tab containers
        const containers = document.querySelectorAll('.tab-content .predictions-container');
        containers.forEach(c => c.innerHTML = '');

        // Get the all-leagues container
        const allLeaguesContainer = document.querySelector('#all-leagues .predictions-container');
        if (allLeaguesContainer) allLeaguesContainer.innerHTML = '';

        // Process each match
        for (const match of matches) {
            // Get league name and map to tab ID
            const leagueName = (match.league_name || match.league || match.competition || match.division || '')
                .toString().toLowerCase().trim();

            // Look up the correct tab ID from mappings
            const tabId = LEAGUE_MAPPINGS[leagueName] || leagueName.replace(/\s+/g, '-').replace(/[^\w-]/g, '');

            // Find the specific league container
            const leagueContainer = document.querySelector(`#${tabId} .predictions-container`) ||
                                  document.querySelector(`.predictions-container[data-league="${tabId}"]`);

            if (!leagueContainer && !allLeaguesContainer) {
                console.debug('[populateLeagueTabsFromMatches] No container found for league:', leagueName, 'tabId:', tabId);
                continue;
            }

            // Create card HTML
            const matchCard = document.createElement('div');
            const confidence = Number(match.confidence || 0);

            // Set confidence class
            let confidenceClass = 'medium-confidence';
            if (confidence >= 75) confidenceClass = 'high-confidence';
            else if (confidence < 60) confidenceClass = 'low-confidence';

            matchCard.className = `prediction-card ${confidenceClass}`;
            matchCard.setAttribute('data-league', tabId);

            // Build card content
            matchCard.innerHTML = `
                <h2 class="match-title">${escapeHtml(match.home_team || match.home || match.team_home || 'Home')} vs ${escapeHtml(match.away_team || match.away || match.team_away || 'Away')}</h2>
                <p class="prediction-detail">Prediction: <span class="ai-pick">${escapeHtml(match.prediction || match.tip || match.ai_pick || '—')}</span></p>
                ${match.odds ? `<p class="odds">${escapeHtml(String(match.odds))}</p>` : ''}
                <p class="match-time">Kickoff: ${escapeHtml(typeof formatTimestamp === 'function' ? formatTimestamp(match.kickoff_time) : (match.kickoff_time || 'TBA'))}</p>
                <div class="confidence">
                    <span>Confidence: ${confidence}%</span>
                    <div class="confidence-bar">
                        <div class="confidence-fill" style="width: ${confidence}%;"></div>
                    </div>
                </div>
            `;

            // Debug mapping for a small sample of matches
            if (Math.random() < 0.05) {
                console.debug('[populateLeagueTabsFromMatches] sample mapping', { leagueName, tabId, home: match.home_team || match.home, away: match.away_team || match.away });
            }

            // Add to specific league container
            if (leagueContainer) {
                leagueContainer.appendChild(matchCard.cloneNode(true));
            } else {
                console.debug('[populateLeagueTabsFromMatches] leagueContainer missing for tabId:', tabId);
            }

            // Add to all-leagues container
            if (allLeaguesContainer) {
                allLeaguesContainer.appendChild(matchCard);
            }
        }
    } catch (err) {
        console.error('[populateLeagueTabsFromMatches] Error populating league tabs:', err);
    }
}

// Small utility to avoid HTML injection when inserting text
function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// ===== Advanced Filtering =====
let activeFilters = {
    date: null,
    prediction: null,
    confidence: null
};

function initializeAdvancedFilters() {
    const filterToggleBtn = document.getElementById('filter-toggle-btn');
    const filterPanel = document.getElementById('advanced-filters-panel');
    const applyFiltersBtn = document.getElementById('apply-filters');
    const clearFiltersBtn = document.getElementById('clear-all-filters');
    const filterOptions = document.querySelectorAll('.filter-option-btn');

    if (!filterToggleBtn || !filterPanel) return;

    filterToggleBtn.addEventListener('click', () => {
        const isVisible = filterPanel.style.display !== 'none';
        filterPanel.style.display = isVisible ? 'none' : 'block';
    });

    filterOptions.forEach(button => {
        button.addEventListener('click', () => {
            const filterType = button.getAttribute('data-filter-type');
            const filterValue = button.getAttribute('data-value');

            document.querySelectorAll(`[data-filter-type="${filterType}"]`).forEach(btn => {
                btn.classList.remove('active');
            });

            button.classList.add('active');
            activeFilters[filterType] = filterValue === 'all' ? null : filterValue;
        });
    });

    if (applyFiltersBtn) {
        applyFiltersBtn.addEventListener('click', () => {
            applyFilters();
            filterPanel.style.display = 'none';
        });
    }

    if (clearFiltersBtn) {
        clearFiltersBtn.addEventListener('click', () => {
            clearAllFilters();
        });
    }
}

function applyFilters() {
    let filteredPredictions = [...allPredictions];

    if (activeFilters.date) {
        filteredPredictions = filterByDate(filteredPredictions, activeFilters.date);
    }

    if (activeFilters.prediction) {
        filteredPredictions = filterByPredictionType(filteredPredictions, activeFilters.prediction);
    }

    if (activeFilters.confidence) {
        filteredPredictions = filterByConfidence(filteredPredictions, activeFilters.confidence);
    }

    displayPredictions(filteredPredictions);
    updateActiveFiltersDisplay();
}

function filterByDate(predictions, dateFilter) {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const tomorrow = new Date(today);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const weekEnd = new Date(today);
    weekEnd.setDate(weekEnd.getDate() + 7);

    return predictions.filter(prediction => {
        const kickoffDate = new Date(prediction.kickoff_time);

        switch(dateFilter) {
            case 'today':
                return kickoffDate >= today && kickoffDate < tomorrow;
            case 'tomorrow':
                const dayAfterTomorrow = new Date(tomorrow);
                dayAfterTomorrow.setDate(dayAfterTomorrow.getDate() + 1);
                return kickoffDate >= tomorrow && kickoffDate < dayAfterTomorrow;
            case 'weekend':
                const dayOfWeek = now.getDay();
                const daysUntilSaturday = (6 - dayOfWeek + 7) % 7;
                const saturday = new Date(today);
                saturday.setDate(saturday.getDate() + daysUntilSaturday);
                const monday = new Date(saturday);
                monday.setDate(monday.getDate() + 2);
                return kickoffDate >= saturday && kickoffDate < monday;
            case 'week':
                return kickoffDate >= today && kickoffDate < weekEnd;
            default:
                return true;
        }
    });
}

function filterByPredictionType(predictions, predictionType) {
    return predictions.filter(prediction => {
        const pred = prediction.prediction.toLowerCase();

        switch(predictionType) {
            case 'win':
                return pred.includes('win');
            case 'draw':
                return pred === 'draw';
            case 'over':
                return pred.includes('over');
            case 'under':
                return pred.includes('under');
            case 'btts':
                return pred.includes('both teams to score') || pred.includes('btts');
            default:
                return true;
        }
    });
}

function filterByConfidence(predictions, confidenceLevel) {
    return predictions.filter(prediction => {
        const confidence = prediction.confidence;

        switch(confidenceLevel) {
            case 'high':
                return confidence >= 75;
            case 'medium':
                return confidence >= 60 && confidence < 75;
            case 'low':
                return confidence < 60;
            default:
                return true;
        }
    });
}

function updateActiveFiltersDisplay() {
    const container = document.getElementById('active-filters-container');
    if (!container) return;

    const filterLabels = {
        date: {
            today: 'Today',
            tomorrow: 'Tomorrow',
            weekend: 'This Weekend',
            week: 'This Week'
        },
        prediction: {
            win: 'Win',
            draw: 'Draw',
            over: 'Over 2.5',
            under: 'Under 2.5',
            btts: 'Both Teams Score'
        },
        confidence: {
            high: 'High Confidence',
            medium: 'Medium Confidence',
            low: 'Low Confidence'
        }
    };

    const chips = [];

    Object.keys(activeFilters).forEach(filterType => {
        if (activeFilters[filterType]) {
            const label = filterLabels[filterType][activeFilters[filterType]];
            chips.push(`
                <div class="filter-chip">
                    <span>${label}</span>
                    <button class="filter-chip-remove" onclick="removeFilter('${filterType}')">×</button>
                </div>
            `);
        }
    });

    container.innerHTML = chips.join('');
}

window.removeFilter = function(filterType) {
    activeFilters[filterType] = null;

    const filterButtons = document.querySelectorAll(`[data-filter-type="${filterType}"]`);
    filterButtons.forEach(btn => btn.classList.remove('active'));

    const allButton = document.querySelector(`[data-filter-type="${filterType}"][data-value="all"]`);
    if (allButton) allButton.classList.add('active');

    applyFilters();
};

function clearAllFilters() {
    activeFilters = {
        date: null,
        prediction: null,
        confidence: null
    };

    document.querySelectorAll('.filter-option-btn').forEach(btn => {
        btn.classList.remove('active');
    });

    document.querySelectorAll('.filter-option-btn[data-value="all"]').forEach(btn => {
        btn.classList.add('active');
    });

    displayPredictions(allPredictions);
    updateActiveFiltersDisplay();
}

// ===== Search / Command Input for Home Page =====
function initializeSearchBar() {
    const input = document.getElementById('predictionSearch');
    const clearBtn = document.getElementById('search-clear-btn');
    const ghost = document.getElementById('search-ghost-text');

    if (!input) return;

    // Improve clear button accessibility and behavior
    if (clearBtn) {
        clearBtn.setAttribute('aria-label', 'Clear search');
        clearBtn.classList.add('search-clear');
        clearBtn.title = 'Clear search (Esc)';
    }

    // Toggle clear button & ghost text visibility on input
    input.addEventListener('input', (e) => {
        const v = e.target.value || '';
        if (clearBtn) clearBtn.style.display = v.trim() ? 'block' : 'none';
        if (ghost) ghost.style.display = v.trim() ? 'none' : 'block';
    });

    // Handle Enter key to run search/commands and Escape to clear
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            handleSearchCommand(input.value);
            return;
        }

        if (e.key === 'Escape') {
            input.value = '';
            if (clearBtn) clearBtn.style.display = 'none';
            if (ghost) ghost.style.display = 'block';
            input.blur();
            displayPredictions(allPredictions);
            updateActiveFiltersDisplay();
            return;
        }
    });

    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            input.value = '';
            clearBtn.style.display = 'none';
            if (ghost) ghost.style.display = 'block';
            input.focus();
            displayPredictions(allPredictions);
            updateActiveFiltersDisplay();
        });
    }
}

function handleSearchCommand(query) {
    const q = (query || '').trim();
    if (!q) {
        displayPredictions(allPredictions);
        return;
    }

    // Commands start with '/'
    if (q.startsWith('/')) {
        const cmd = q.slice(1).toLowerCase();

        // /c75 -> filter by confidence >= 75
        const confidenceMatch = cmd.match(/^c(\d{1,3})$/);
        if (confidenceMatch) {
            const threshold = parseInt(confidenceMatch[1], 10);
            const filtered = allPredictions.filter(p => Number(p.confidence) >= threshold);
            displayPredictions(filtered);
            updateActiveFiltersDisplay();
            return;
        }

        // /odds -> sort by odds desc
        if (cmd === 'odds') {
            const sorted = [...allPredictions].sort((a, b) => (Number(b.odds) || 0) - (Number(a.odds) || 0));
            displayPredictions(sorted);
            updateActiveFiltersDisplay();
            return;
        }

        // /league:<name> -> filter by league (partial match)
        const leagueMatch = cmd.match(/^league:(.+)$/);
        if (leagueMatch) {
            const name = leagueMatch[1].trim().toLowerCase();
            const filtered = allPredictions.filter(p => (p.league || '').toLowerCase().includes(name));
            displayPredictions(filtered);
            updateActiveFiltersDisplay();
            return;
        }

        // /type:<win|draw|over|under|btts>
        const typeMatch = cmd.match(/^type:(win|draw|over|under|btts)$/);
        if (typeMatch) {
            const t = typeMatch[1];
            const filtered = filterByPredictionType(allPredictions, t);
            displayPredictions(filtered);
            updateActiveFiltersDisplay();
            return;
        }

        // /top:<n> -> top n predictions by confidence
        const topMatch = cmd.match(/^top:(\d{1,3})$/);
        if (topMatch) {
            const n = Math.max(1, Math.min(100, parseInt(topMatch[1], 10)));
            const sorted = [...allPredictions].sort((a, b) => (Number(b.confidence) || 0) - (Number(a.confidence) || 0)).slice(0, n);
            displayPredictions(sorted);
            updateActiveFiltersDisplay();
            return;
        }

        // /clear -> reset search and filters
        if (cmd === 'clear') {
            displayPredictions(allPredictions);
            updateActiveFiltersDisplay();
            return;
        }

        // Unknown command: show all and log hint
        console.info('Unknown command:', cmd);
        displayPredictions(allPredictions);
        updateActiveFiltersDisplay();
        return;
    }

    // Normal search: match home/away team, league, or prediction text
    const term = q.toLowerCase();
    const filtered = allPredictions.filter(p => {
        return (p.home_team && p.home_team.toLowerCase().includes(term)) ||
               (p.away_team && p.away_team.toLowerCase().includes(term)) ||
               (p.league && p.league.toLowerCase().includes(term)) ||
               (p.prediction && p.prediction.toLowerCase().includes(term));
    });

    displayPredictions(filtered);
    updateActiveFiltersDisplay();
}

function initializeLeagueTabs() {
    console.log('Initializing league tabs...');
    const tabButtons = document.querySelectorAll('.tab-btn[data-tab]');
    const tabContents = document.querySelectorAll('.tab-content');

    console.log('Found tab buttons:', tabButtons.length);
    console.log('Found tab contents:', tabContents.length);

    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const targetTab = button.getAttribute('data-tab');
            console.log('Tab clicked:', targetTab);

            // Update active button
            tabButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');

            // Update active content
            tabContents.forEach(content => {
                content.classList.remove('active');
                if (content.id === targetTab) {
                    content.classList.add('active');
                    console.log('Activated tab content:', targetTab);
                }
            });
        });
    });
}
