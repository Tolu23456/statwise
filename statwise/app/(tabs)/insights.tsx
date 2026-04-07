import React from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity,
  ActivityIndicator, Platform, RefreshControl,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQuery } from '@tanstack/react-query';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import * as Haptics from 'expo-haptics';
import { Colors } from '@/constants/colors';
import { supabase } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import { useTheme } from '@/context/ThemeContext';

type Insight = {
  id: string;
  title: string;
  summary: string;
  content?: string;
  published_at: string;
  tier_required: string;
  category?: string;
};

const ALLOWED_TIERS = ['VIP Tier', 'VVIP Tier'];

const MOCK_INSIGHTS: Insight[] = [
  {
    id: '1', title: 'The Underdog Advantage', category: 'Strategy',
    summary: 'We explore the statistical patterns of underdog victories and how to spot high-value opportunities in modern football.',
    content: `## The Underdog Advantage\n\nIn football betting, the concept of "value" is everything. When a team is underestimated by the market, you get an edge.\n\n### Key Findings\n\n- Underdogs with a 30–40% win probability are mispriced 28% of the time in our model\n- Home underdogs outperform expectations by +4.2% ROI on average\n- Teams with 3+ consecutive losses are systematically undervalued\n\n### How to Use This\n\nLook for matches where our model confidence is 10%+ higher than implied odds. That gap is where the value lives.\n\nFocus on mid-table teams playing at home against top-4 sides after an international break — historically the highest edge scenario.`,
    published_at: new Date(Date.now() - 86400000 * 3).toISOString(), tier_required: 'VIP Tier',
  },
  {
    id: '2', title: 'Market Trends: Q1 Predictions', category: 'Analysis',
    summary: 'Our models project a significant shift in European league dynamics. Here\'s what to watch out for.',
    content: `## Market Trends Q1\n\n### What's Changing\n\nThe European market is showing three major trends this quarter:\n\n1. **Under-inflation in La Liga** — goals per game have dropped 0.4 compared to last season. Over/Under lines haven't adjusted yet.\n\n2. **Home advantage erosion** — Post-COVID home advantage data is now fully factored in by our model. Many books still use pre-2020 baselines.\n\n3. **Champions League group stage value** — Heavily favored teams are increasingly being rested mid-competition. Watch for lineup news.\n\n### Recommended Leagues\n\nBest edge currently: Eredivisie (home teams +6.1% ROI) and Brazilian Série A (unders outperforming by 11%).`,
    published_at: new Date(Date.now() - 86400000 * 5).toISOString(), tier_required: 'VIP Tier',
  },
  {
    id: '3', title: 'Over/Under: The Hidden Edge', category: 'Tactics',
    summary: 'Why most bettors ignore over/under markets and how you can profit from this systematic blind spot.',
    content: `## Over/Under: The Hidden Edge\n\n### The Blind Spot\n\nMost casual bettors focus on match winner markets. This concentrates public money and compresses value. The Over/Under (goals) market receives less attention — and is mispriced more often.\n\n### Our Model's Edge\n\nUsing Poisson goal modeling with team-specific attack/defense ratings, our Over/Under accuracy over the last 6 months:\n\n- **Over 2.5 predictions**: 61.4% hit rate (break-even is ~52%)\n- **Under 2.5 predictions**: 58.9% hit rate\n\n### Best Scenarios for Unders\n\n- Both teams in bottom-half defensive form\n- Mid-week cup matches following weekend exertion\n- Rivalry derbies (historically low-scoring due to tactical caution)\n\n### Best Scenarios for Overs\n\n- Both teams' last 5 home/away averages above 2.8 goals\n- Teams with nothing to play for (end of season)\n- Matches with odds_home below 1.60 (dominant team plays open)`,
    published_at: new Date(Date.now() - 86400000 * 7).toISOString(), tier_required: 'VIP Tier',
  },
];

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const days = Math.floor(diff / 86400000);
  if (days === 0) return 'Today';
  if (days === 1) return '1 day ago';
  return `${days} days ago`;
}

function estimateReadTime(content?: string): string {
  if (!content) return '2 min read';
  const words = content.split(/\s+/).length;
  const mins = Math.max(1, Math.round(words / 200));
  return `${mins} min read`;
}

export default function InsightsScreen() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const { profile } = useAuth();
  const router = useRouter();

  const hasAccess = ALLOWED_TIERS.includes(profile?.current_tier ?? '');
  const topInset = Platform.OS === 'web' ? 0 : insets.top;

  const { data: insights = [], isLoading, refetch, isFetching } = useQuery<Insight[]>({
    queryKey: ['insights'],
    queryFn: async () => {
      const { data, error } = await supabase
        .from('insights')
        .select('*')
        .order('published_at', { ascending: false });
      if (error) return MOCK_INSIGHTS;
      return data?.length ? data : MOCK_INSIGHTS;
    },
    enabled: hasAccess,
  });

  function handleReadMore(item: Insight) {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    router.push({
      pathname: '/insight-detail',
      params: { insight: JSON.stringify(item) },
    });
  }

  if (!hasAccess) {
    return (
      <View style={[styles.lockContainer, { backgroundColor: C.background, paddingTop: topInset + 20 }]}>
        <View style={[styles.lockCard, { backgroundColor: C.card, borderColor: C.border }]}>
          <View style={[styles.lockIconBg, { backgroundColor: C.goldLight }]}>
            <Ionicons name="lock-closed" size={36} color={C.gold} />
          </View>
          <Text style={[styles.lockTitle, { color: C.text }]}>VIP Exclusive Content</Text>
          <Text style={[styles.lockDesc, { color: C.textSecondary }]}>
            Unlock deep-dive analysis, market trends, and expert insights with a VIP or VVIP subscription.
          </Text>

          <View style={styles.lockFeatureList}>
            {['Expert match analysis reports', 'Market trend breakdowns', 'Betting strategy guides', 'Weekly ROI reviews'].map(f => (
              <View key={f} style={styles.lockFeatureRow}>
                <Ionicons name="checkmark-circle" size={16} color={C.gold} />
                <Text style={[styles.lockFeatureText, { color: C.textSecondary }]}>{f}</Text>
              </View>
            ))}
          </View>

          <TouchableOpacity
            style={[styles.upgradeBtn, { backgroundColor: C.gold }]}
            onPress={() => {
              Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
              router.push('/(tabs)/subscriptions');
            }}
            activeOpacity={0.85}
          >
            <Ionicons name="diamond" size={16} color="#fff" />
            <Text style={styles.upgradeBtnText}>Upgrade to VIP</Text>
          </TouchableOpacity>
        </View>

        <Text style={[styles.previewLabel, { color: C.textSecondary }]}>Preview — available with VIP:</Text>
        {MOCK_INSIGHTS.map(insight => (
          <TouchableOpacity
            key={insight.id}
            style={[styles.previewCard, { backgroundColor: C.card, borderColor: C.border }]}
            onPress={() => router.push('/(tabs)/subscriptions')}
            activeOpacity={0.8}
          >
            <View style={[styles.previewOverlay, { backgroundColor: C.overlay }]}>
              <View style={[styles.previewUnlockBadge, { backgroundColor: C.gold }]}>
                <Ionicons name="lock-closed" size={11} color="#fff" />
                <Text style={styles.previewUnlockText}>Unlock with VIP</Text>
              </View>
            </View>
            {insight.category && (
              <View style={[styles.catBadge, { backgroundColor: C.goldLight }]}>
                <Text style={[styles.catText, { color: C.gold }]}>{insight.category}</Text>
              </View>
            )}
            <Text style={[styles.previewTitle, { color: C.text }]}>{insight.title}</Text>
            <Text style={[styles.previewSummary, { color: C.textSecondary }]} numberOfLines={2}>
              {insight.summary}
            </Text>
            <Text style={[styles.previewDate, { color: C.textMuted }]}>
              {timeAgo(insight.published_at)} · {estimateReadTime(insight.content)}
            </Text>
          </TouchableOpacity>
        ))}
      </View>
    );
  }

  return (
    <View style={[styles.container, { backgroundColor: C.background }]}>
      <View style={[styles.header, { paddingTop: topInset + 8 }]}>
        <Text style={[styles.title, { color: C.text }]}>Exclusive Insights</Text>
        <View style={[styles.vipBadge, { backgroundColor: C.goldLight }]}>
          <Ionicons name="diamond" size={12} color={C.gold} />
          <Text style={[styles.vipText, { color: C.gold }]}>VIP</Text>
        </View>
      </View>

      {isLoading ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={C.primary} />
        </View>
      ) : (
        <FlatList
          data={insights}
          keyExtractor={item => item.id}
          contentContainerStyle={[
            styles.list,
            { paddingBottom: insets.bottom + (Platform.OS === 'web' ? 24 : 80) },
          ]}
          refreshControl={
            <RefreshControl refreshing={isFetching} onRefresh={refetch} tintColor={C.primary} colors={[C.primary]} />
          }
          renderItem={({ item }) => (
            <View style={[styles.card, { backgroundColor: C.card, borderColor: C.border }]}>
              <View style={styles.cardTopRow}>
                {item.category && (
                  <View style={[styles.catBadge, { backgroundColor: C.goldLight }]}>
                    <Text style={[styles.catText, { color: C.gold }]}>{item.category}</Text>
                  </View>
                )}
                <Text style={[styles.readTime, { color: C.textMuted }]}>
                  {estimateReadTime(item.content)}
                </Text>
              </View>
              <Text style={[styles.cardTitle, { color: C.text }]}>{item.title}</Text>
              <Text style={[styles.cardSummary, { color: C.textSecondary }]} numberOfLines={3}>
                {item.summary}
              </Text>
              <View style={styles.cardFooter}>
                <Text style={[styles.cardDate, { color: C.textMuted }]}>
                  {timeAgo(item.published_at)}
                </Text>
                <TouchableOpacity
                  style={[styles.readBtn, { backgroundColor: C.primaryLight }]}
                  onPress={() => handleReadMore(item)}
                >
                  <Text style={[styles.readBtnText, { color: C.primary }]}>Read More</Text>
                  <Ionicons name="arrow-forward" size={13} color={C.primary} />
                </TouchableOpacity>
              </View>
            </View>
          )}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Ionicons name="analytics-outline" size={44} color={C.textMuted} />
              <Text style={[styles.emptyText, { color: C.textSecondary }]}>No insights yet. Check back soon.</Text>
            </View>
          }
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    paddingHorizontal: 16, paddingBottom: 12,
  },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold', flex: 1 },
  vipBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 10, paddingVertical: 4, borderRadius: 20,
  },
  vipText: { fontSize: 12, fontFamily: 'Inter_700Bold' },
  list: { paddingHorizontal: 16, paddingTop: 4 },
  card: { borderRadius: 16, borderWidth: 1, padding: 16, marginBottom: 12 },
  cardTopRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  catBadge: { alignSelf: 'flex-start', paddingHorizontal: 10, paddingVertical: 3, borderRadius: 20 },
  catText: { fontSize: 11, fontFamily: 'Inter_600SemiBold', textTransform: 'uppercase', letterSpacing: 0.5 },
  readTime: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  cardTitle: { fontSize: 17, fontFamily: 'Inter_700Bold', marginBottom: 8, lineHeight: 24 },
  cardSummary: { fontSize: 14, fontFamily: 'Inter_400Regular', lineHeight: 20, marginBottom: 14 },
  cardFooter: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  cardDate: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  readBtn: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 14, paddingVertical: 6, borderRadius: 8 },
  readBtnText: { fontSize: 13, fontFamily: 'Inter_600SemiBold' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  empty: { alignItems: 'center', paddingTop: 60, gap: 12 },
  emptyText: { fontSize: 15, fontFamily: 'Inter_400Regular', textAlign: 'center' },
  lockContainer: { flex: 1, paddingHorizontal: 16 },
  lockCard: {
    borderRadius: 20, borderWidth: 1, padding: 24,
    alignItems: 'center', marginBottom: 20,
  },
  lockIconBg: {
    width: 80, height: 80, borderRadius: 40,
    alignItems: 'center', justifyContent: 'center', marginBottom: 16,
  },
  lockTitle: { fontSize: 22, fontFamily: 'Inter_700Bold', marginBottom: 10, textAlign: 'center' },
  lockDesc: { fontSize: 14, fontFamily: 'Inter_400Regular', textAlign: 'center', lineHeight: 21, marginBottom: 16 },
  lockFeatureList: { alignSelf: 'stretch', gap: 8, marginBottom: 20 },
  lockFeatureRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  lockFeatureText: { fontSize: 14, fontFamily: 'Inter_400Regular', flex: 1 },
  upgradeBtn: { paddingHorizontal: 28, paddingVertical: 14, borderRadius: 14, flexDirection: 'row', alignItems: 'center', gap: 8 },
  upgradeBtnText: { color: '#fff', fontSize: 16, fontFamily: 'Inter_600SemiBold' },
  previewLabel: { fontSize: 13, fontFamily: 'Inter_500Medium', marginBottom: 10 },
  previewCard: { borderRadius: 14, borderWidth: 1, padding: 14, marginBottom: 10, overflow: 'hidden' },
  previewOverlay: {
    ...StyleSheet.absoluteFillObject, zIndex: 1,
    alignItems: 'center', justifyContent: 'center',
  },
  previewUnlockBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    paddingHorizontal: 14, paddingVertical: 8, borderRadius: 20,
  },
  previewUnlockText: { color: '#fff', fontSize: 13, fontFamily: 'Inter_600SemiBold' },
  previewTitle: { fontSize: 16, fontFamily: 'Inter_600SemiBold', marginBottom: 4 },
  previewSummary: { fontSize: 13, fontFamily: 'Inter_400Regular', lineHeight: 18, marginBottom: 6 },
  previewDate: { fontSize: 12, fontFamily: 'Inter_400Regular' },
});
