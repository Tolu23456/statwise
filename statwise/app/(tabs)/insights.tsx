import React from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity,
  useColorScheme, ActivityIndicator, Platform,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQuery } from '@tanstack/react-query';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { Colors } from '@/constants/colors';
import { supabase } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';

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

export default function InsightsScreen() {
  const scheme = useColorScheme() ?? 'dark';
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const { profile } = useAuth();
  const router = useRouter();

  const hasAccess = ALLOWED_TIERS.includes(profile?.current_tier ?? '');
  const topInset = Platform.OS === 'web' ? 67 : insets.top;

  const { data: insights = [], isLoading } = useQuery<Insight[]>({
    queryKey: ['insights'],
    queryFn: async () => {
      const { data, error } = await supabase
        .from('insights')
        .select('*')
        .order('published_at', { ascending: false });
      if (error) {
        return MOCK_INSIGHTS;
      }
      return data?.length ? data : MOCK_INSIGHTS;
    },
    enabled: hasAccess,
  });

  if (!hasAccess) {
    return (
      <View style={[styles.lockContainer, { backgroundColor: C.background, paddingTop: topInset + 20 }]}>
        <View style={[styles.lockCard, { backgroundColor: C.card, borderColor: C.border }]}>
          <Ionicons name="lock-closed" size={44} color={C.gold} />
          <Text style={[styles.lockTitle, { color: C.text }]}>VIP Exclusive</Text>
          <Text style={[styles.lockDesc, { color: C.textSecondary }]}>
            Unlock deep-dive analysis, market trends, and expert insights with a VIP or VVIP subscription.
          </Text>
          <TouchableOpacity
            style={[styles.upgradeBtn, { backgroundColor: C.gold }]}
            onPress={() => router.push('/(tabs)/subscriptions')}
          >
            <Text style={styles.upgradeBtnText}>Upgrade to VIP</Text>
          </TouchableOpacity>
        </View>
        <View style={styles.previewSection}>
          <Text style={[styles.previewLabel, { color: C.textSecondary }]}>What you'll get:</Text>
          {MOCK_INSIGHTS.map(insight => (
            <View key={insight.id} style={[styles.previewCard, { backgroundColor: C.card, borderColor: C.border, opacity: 0.5 }]}>
              <View style={[styles.blurOverlay, { backgroundColor: C.overlay }]} />
              <Text style={[styles.previewTitle, { color: C.text }]}>{insight.title}</Text>
              <Text style={[styles.previewDate, { color: C.textSecondary }]}>
                {new Date(insight.published_at).toLocaleDateString()}
              </Text>
            </View>
          ))}
        </View>
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
            { paddingBottom: insets.bottom + (Platform.OS === 'web' ? 84 : 80) },
          ]}
          renderItem={({ item }) => (
            <View style={[styles.card, { backgroundColor: C.card, borderColor: C.border }]}>
              {item.category && (
                <View style={[styles.catBadge, { backgroundColor: C.goldLight }]}>
                  <Text style={[styles.catText, { color: C.gold }]}>{item.category}</Text>
                </View>
              )}
              <Text style={[styles.cardTitle, { color: C.text }]}>{item.title}</Text>
              <Text style={[styles.cardSummary, { color: C.textSecondary }]} numberOfLines={3}>
                {item.summary}
              </Text>
              <View style={styles.cardFooter}>
                <Text style={[styles.cardDate, { color: C.textMuted }]}>
                  {new Date(item.published_at).toLocaleDateString('en-US', {
                    month: 'long', day: 'numeric', year: 'numeric',
                  })}
                </Text>
                <TouchableOpacity style={[styles.readBtn, { backgroundColor: C.primaryLight }]}>
                  <Text style={[styles.readBtnText, { color: C.primary }]}>Read More</Text>
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

const MOCK_INSIGHTS: Insight[] = [
  {
    id: '1', title: 'The Underdog Advantage', category: 'Strategy',
    summary: 'We explore the statistical patterns of underdog victories and how to spot high-value opportunities in modern football...',
    published_at: new Date(Date.now() - 86400000 * 3).toISOString(), tier_required: 'VIP Tier',
  },
  {
    id: '2', title: 'Market Trends: Q1 Predictions', category: 'Analysis',
    summary: 'Our models project a significant shift in European league dynamics. Here\'s what to watch out for...',
    published_at: new Date(Date.now() - 86400000 * 5).toISOString(), tier_required: 'VIP Tier',
  },
  {
    id: '3', title: 'Over/Under: The Hidden Edge', category: 'Tactics',
    summary: 'Why most bettors ignore over/under markets and how you can profit from this systematic blind spot...',
    published_at: new Date(Date.now() - 86400000 * 7).toISOString(), tier_required: 'VIP Tier',
  },
];

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
  catBadge: { alignSelf: 'flex-start', paddingHorizontal: 10, paddingVertical: 3, borderRadius: 20, marginBottom: 10 },
  catText: { fontSize: 11, fontFamily: 'Inter_600SemiBold', textTransform: 'uppercase', letterSpacing: 0.5 },
  cardTitle: { fontSize: 17, fontFamily: 'Inter_700Bold', marginBottom: 8, lineHeight: 24 },
  cardSummary: { fontSize: 14, fontFamily: 'Inter_400Regular', lineHeight: 20, marginBottom: 14 },
  cardFooter: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  cardDate: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  readBtn: { paddingHorizontal: 14, paddingVertical: 6, borderRadius: 8 },
  readBtnText: { fontSize: 13, fontFamily: 'Inter_600SemiBold' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  empty: { alignItems: 'center', paddingTop: 60, gap: 12 },
  emptyText: { fontSize: 15, fontFamily: 'Inter_400Regular', textAlign: 'center' },
  lockContainer: { flex: 1, paddingHorizontal: 16 },
  lockCard: {
    borderRadius: 20, borderWidth: 1, padding: 28,
    alignItems: 'center', marginBottom: 24,
  },
  lockTitle: { fontSize: 22, fontFamily: 'Inter_700Bold', marginTop: 16, marginBottom: 10 },
  lockDesc: { fontSize: 15, fontFamily: 'Inter_400Regular', textAlign: 'center', lineHeight: 22, marginBottom: 24 },
  upgradeBtn: { paddingHorizontal: 28, paddingVertical: 14, borderRadius: 14 },
  upgradeBtnText: { color: '#fff', fontSize: 16, fontFamily: 'Inter_600SemiBold' },
  previewSection: { gap: 10 },
  previewLabel: { fontSize: 13, fontFamily: 'Inter_500Medium', marginBottom: 4 },
  previewCard: { borderRadius: 14, borderWidth: 1, padding: 14, overflow: 'hidden' },
  blurOverlay: { ...StyleSheet.absoluteFillObject, zIndex: 1 },
  previewTitle: { fontSize: 16, fontFamily: 'Inter_600SemiBold' },
  previewDate: { fontSize: 12, fontFamily: 'Inter_400Regular', marginTop: 4 },
});
