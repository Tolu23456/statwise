import { supabase } from '../../env.js';
import { showUpgradeModal } from '../../utils.js';

let verifiedTier;

export async function initializeInsightsPage(userTier) {
    verifiedTier = userTier;
    // VIP and VVIP tier only
    if (!hasAccess('VIP Tier')) {
        showUpgradeModal('VIP Tier');
        return;
    }

    await loadInsights();
}

async function loadInsights() {
    try {
        const { data: accuracy, error } = await supabase
            .from('prediction_accuracy')
            .select('*')
            .order('date', { ascending: false })
            .limit(30);

        if (error) {
            console.warn('Error loading insights:', error);
            return;
        }

        displayInsights(accuracy || []);
    } catch (error) {
        console.error('Error loading insights:', error);
    }
}

function displayInsights(accuracy) {
    const container = document.getElementById('insights-container');
    if (!container) return;

    const totalPredictions = accuracy.reduce((sum, day) => sum + (day.total_predictions || 0), 0);
    const correctPredictions = accuracy.reduce((sum, day) => sum + (day.correct_predictions || 0), 0);
    const overallAccuracy = totalPredictions > 0 ? (correctPredictions / totalPredictions * 100).toFixed(1) : 0;

    container.innerHTML = `
        <div class="insights-section">
            <div class="insights-header">
                <h3>AI Prediction Performance</h3>
                <div class="overall-stats">
                    <div class="stat-card">
                        <h4>Overall Accuracy</h4>
                        <span class="stat-value">${overallAccuracy}%</span>
                    </div>
                    <div class="stat-card">
                        <h4>Total Predictions</h4>
                        <span class="stat-value">${totalPredictions}</span>
                    </div>
                    <div class="stat-card">
                        <h4>Correct Predictions</h4>
                        <span class="stat-value">${correctPredictions}</span>
                    </div>
                </div>
            </div>

            <div class="accuracy-chart">
                <h4>Recent Performance</h4>
                ${accuracy.length === 0 ? `
                    <p>No performance data available yet.</p>
                ` : `
                    <div class="chart-data">
                        ${accuracy.slice(0, 7).map(day => {
                            const dayAccuracy = day.total_predictions > 0 ?
                                (day.correct_predictions / day.total_predictions * 100).toFixed(1) : 0;
                            return `
                                <div class="chart-bar">
                                    <div class="bar" style="height: ${dayAccuracy}%"></div>
                                    <span class="date">${new Date(day.date).toLocaleDateString()}</span>
                                    <span class="accuracy">${dayAccuracy}%</span>
                                </div>
                            `;
                        }).join('')}
                    </div>
                `}
            </div>
        </div>
    `;
}

function hasAccess(requiredTier) {
    const tierLevels = {
        'Free Tier': 0,
        'Premium Tier': 1,
        'VIP Tier': 2,
        'VVIP Tier': 3
    };

    const currentLevel = tierLevels[verifiedTier] || 0;
    const requiredLevel = tierLevels[requiredTier] || 0;

    return currentLevel >= requiredLevel;
}
