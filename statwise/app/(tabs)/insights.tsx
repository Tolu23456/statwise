import React, { useCallback, useRef, useState } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity,
  Platform, RefreshControl, ScrollView,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQuery } from '@tanstack/react-query';
import Ionicons from '@expo/vector-icons/Ionicons';
import { useRouter, useFocusEffect } from 'expo-router';
import * as Haptics from 'expo-haptics';
import { Colors } from '@/constants/colors';
import { supabase } from '@/lib/supabase';
import { QUERY_KEYS, STALE_TIME } from '@/lib/queryConfig';
import { useAuth } from '@/context/AuthContext';
import { useTheme } from '@/context/ThemeContext';
import {
  ALL_CATEGORIES,
  INSIGHTS_LIBRARY,
  INSIGHT_TIERS,
  filterInsights,
  insightCategories,
  type Insight,
} from '@/lib/insightsCore';
import { AnimatedEntrance } from '@/components/AnimatedEntrance';
import { SkeletonInsightCard } from '@/components/Skeletons';

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

  const hasAccess = INSIGHT_TIERS.includes(profile?.current_tier ?? '');
  const topInset = Platform.OS === 'web' ? 0 : insets.top;
  // Category filter — derived from whatever list loaded (library or DB),
  // so chips always match the data actually on screen.
  const [category, setCategory] = useState(ALL_CATEGORIES);

  const { data: insights = [], isLoading, refetch, isFetching } = useQuery<Insight[]>({
    queryKey: QUERY_KEYS.insights(),
    queryFn: async () => {
      const { data, error } = await supabase
        .from('insights')
        .select('*')
        .order('published_at', { ascending: false });
      if (error) return INSIGHTS_LIBRARY;
      return data?.length ? data : INSIGHTS_LIBRARY;
    },
    enabled: hasAccess,
    // Plan 4.1 — insights only move when the model writes; no focus refetch.
    staleTime: STALE_TIME.aggregate,
  });

  const categories = insightCategories(insights);
  const visible = filterInsights(insights, category);

  // Re-run the entrance animation EVERY time the user comes to this tab
  // (screens stay mounted in the tab navigator, so a mount-only entering
  // animation would only ever play once). The initial focus is skipped —
  // the first mount already animates; only refocuses bump the key.
  const firstFocus = useRef(true);
  const [visitKey, setVisitKey] = useState(0);
  useFocusEffect(
    useCallback(() => {
      if (firstFocus.current) {
        firstFocus.current = false;
        return;
      }
      setVisitKey(k => k + 1);
    }, []),
  );

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
        {INSIGHTS_LIBRARY.map(insight => (
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
        <View style={styles.list}>
          {[0, 1, 2].map(i => (
            <SkeletonInsightCard key={i} />
          ))}
        </View>
      ) : (
        <>
          {categories.length > 2 && (
            // Own layer (zIndex) + no shrink: the chip bar is chrome, list
            // content must never paint over or squeeze it.
            <View style={[styles.chipsWrap, { backgroundColor: C.background }]}>
              <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={styles.chipRow}
              >
                {categories.map(cat => {
                  const active = cat === category;
                  return (
                    <TouchableOpacity
                      key={cat}
                      style={[
                        styles.chip,
                        { backgroundColor: active ? C.primary : C.inputBg, borderColor: active ? C.primary : C.border },
                      ]}
                      onPress={() => {
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                        setCategory(cat);
                      }}
                      activeOpacity={0.8}
                    >
                      <Text
                        style={[styles.chipText, { color: active ? '#fff' : C.textSecondary }]}
                      >
                        {cat}
                      </Text>
                    </TouchableOpacity>
                  );
                })}
              </ScrollView>
            </View>
          )}
          <FlatList
            key={`insights-${visitKey}`}
            style={styles.listFlex}
            data={visible}
            keyExtractor={item => item.id}
          contentContainerStyle={[
            styles.list,
            { paddingBottom: insets.bottom + (Platform.OS === 'web' ? 24 : 80) },
          ]}
          refreshControl={
            <RefreshControl refreshing={isFetching} onRefresh={refetch} tintColor={C.primary} colors={[C.primary]} />
          }
          renderItem={({ item, index }) => (
            <AnimatedEntrance index={index}>
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
            </AnimatedEntrance>
          )}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Ionicons name="analytics-outline" size={44} color={C.textMuted} />
              <Text style={[styles.emptyText, { color: C.textSecondary }]}>No insights yet. Check back soon.</Text>
            </View>
          }
          />
        </>
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
  // Category filter chips (horizontal scroll under the header).
  chipsWrap: { flexShrink: 0, zIndex: 2 },
  listFlex: { flex: 1 },
  chipRow: { paddingHorizontal: 16, paddingBottom: 10, gap: 8 },
  chip: {
    paddingHorizontal: 14, paddingVertical: 7, borderRadius: 18,
    borderWidth: 1, alignSelf: 'flex-start',
  },
  chipText: { fontSize: 13, fontFamily: 'Inter_600SemiBold' },
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
