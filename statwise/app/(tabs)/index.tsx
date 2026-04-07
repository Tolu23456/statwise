import React, { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity, TextInput,
  RefreshControl, ScrollView, Platform,
  Animated,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { Colors } from '@/constants/colors';
import { supabase, Prediction } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import { useTheme } from '@/context/ThemeContext';
import { PredictionCard } from '@/components/PredictionCard';
import { PredictionDetailModal } from '@/components/PredictionDetailModal';
import { SkeletonCard } from '@/components/SkeletonCard';
import { OnboardingModal, shouldShowOnboarding } from '@/components/OnboardingModal';
import AsyncStorage from '@react-native-async-storage/async-storage';

const POLL_INTERVAL_MS = 20 * 60 * 1000;

const LEAGUES = [
  { slug: 'all', name: 'All' },
  { slug: 'premier-league', name: 'Premier League' },
  { slug: 'la-liga', name: 'La Liga' },
  { slug: 'bundesliga', name: 'Bundesliga' },
  { slug: 'serie-a', name: 'Serie A' },
  { slug: 'ligue1', name: 'Ligue 1' },
  { slug: 'champions-league', name: 'UCL' },
  { slug: 'mls', name: 'MLS' },
  { slug: 'saudi-pro-league', name: 'Saudi Pro' },
  { slug: 'copa-libertadores', name: 'Libertadores' },
  { slug: 'brazilian-serie-a', name: 'Brasileirao' },
  { slug: 'argentina-primera', name: 'Argentina' },
  { slug: 'turkish-super-lig', name: 'Süper Lig' },
  { slug: 'j-league', name: 'J-League' },
  { slug: 'liga-mx', name: 'Liga MX' },
  { slug: 'efl-championship', name: 'Championship' },
];

const TIER_LIMITS: Record<string, number> = {
  'Free Tier': 5,
  'Premium Tier': 25,
  'VIP Tier': 75,
  'VVIP Tier': Infinity,
};

const TIER_DB_ALLOWED: Record<string, string[]> = {
  'Free Tier':    ['free'],
  'Premium Tier': ['free', 'premium'],
  'VIP Tier':     ['free', 'premium', 'vip'],
  'VVIP Tier':    ['free', 'premium', 'vip', 'vvip'],
};

const TIER_NEXT: Record<string, string> = {
  'Free Tier': 'Premium Tier',
  'Premium Tier': 'VIP Tier',
  'VIP Tier': 'VVIP Tier',
  'VVIP Tier': 'VVIP Tier',
};

function sendBrowserNotification(title: string, body: string) {
  if (Platform.OS === 'web' && 'Notification' in window && Notification.permission === 'granted') {
    new Notification(title, { body, icon: '/favicon.ico' });
  }
}

export default function HomeScreen() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const { profile } = useAuth();
  const queryClient = useQueryClient();

  const [selectedLeague, setSelectedLeague] = useState('all');
  const [search, setSearch] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [newPredictionsBadge, setNewPredictionsBadge] = useState(0);
  const [selectedPrediction, setSelectedPrediction] = useState<Prediction | null>(null);
  const [showOnboarding, setShowOnboarding] = useState(false);

  const badgeFade = useRef(new Animated.Value(0)).current;
  const pulseAnim = useRef(new Animated.Value(1)).current;

  const userTier = profile?.current_tier ?? 'Free Tier';
  const userLimit = TIER_LIMITS[userTier] ?? 5;
  const nextTier = TIER_NEXT[userTier] ?? 'Premium Tier';
  const allowedTiers = TIER_DB_ALLOWED[userTier] ?? ['free'];

  useEffect(() => {
    shouldShowOnboarding().then(show => {
      if (show) setShowOnboarding(true);
    });
  }, []);

  const { data: predictions = [], isLoading, isError, refetch } = useQuery<Prediction[]>({
    queryKey: ['predictions', userTier],
    queryFn: async () => {
      const today = new Date().toISOString().split('T')[0];
      const { data, error } = await supabase
        .from('predictions')
        .select('*')
        .gte('match_date', today)
        .in('tier', allowedTiers)
        .order('confidence', { ascending: false })
        .limit(userLimit === Infinity ? 500 : userLimit);
      if (error) throw error;
      return data ?? [];
    },
    refetchInterval: POLL_INTERVAL_MS,
    refetchIntervalInBackground: false,
    staleTime: 5 * 60 * 1000,
  });

  useEffect(() => {
    const channel = supabase
      .channel('predictions_realtime')
      .on('postgres_changes', {
        event: '*',
        schema: 'public',
        table: 'predictions',
      }, () => {
        setNewPredictionsBadge(prev => prev + 1);
        Animated.sequence([
          Animated.timing(badgeFade, { toValue: 1, duration: 300, useNativeDriver: true }),
          Animated.delay(5000),
          Animated.timing(badgeFade, { toValue: 0, duration: 600, useNativeDriver: true }),
        ]).start();
        queryClient.invalidateQueries({ queryKey: ['predictions', userTier] });
        sendBrowserNotification('StatWise', 'New predictions are available!');
      })
      .subscribe();

    return () => { supabase.removeChannel(channel); };
  }, [queryClient, userTier]);

  useEffect(() => {
    const pulse = Animated.loop(
      Animated.sequence([
        Animated.timing(pulseAnim, { toValue: 1.04, duration: 600, useNativeDriver: true }),
        Animated.timing(pulseAnim, { toValue: 1, duration: 600, useNativeDriver: true }),
      ]),
    );
    if (newPredictionsBadge > 0) {
      pulse.start();
    } else {
      pulse.stop();
      pulseAnim.setValue(1);
    }
    return () => pulse.stop();
  }, [newPredictionsBadge, pulseAnim]);

  const dismissBadge = useCallback(() => {
    setNewPredictionsBadge(0);
    Animated.timing(badgeFade, { toValue: 0, duration: 300, useNativeDriver: true }).start();
  }, [badgeFade]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await refetch();
    setNewPredictionsBadge(0);
    setRefreshing(false);
  }, [refetch]);

  const filtered = useMemo(() => {
    let list = predictions;
    if (selectedLeague !== 'all') {
      list = list.filter(p => p.league_slug === selectedLeague);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        p =>
          p.match_title?.toLowerCase().includes(q) ||
          p.league?.toLowerCase().includes(q) ||
          p.prediction?.toLowerCase().includes(q),
      );
    }
    return list;
  }, [predictions, selectedLeague, search]);

  const topInset = Platform.OS === 'web' ? 67 : insets.top;

  const EmptyState = () => {
    if (search) {
      return (
        <View style={styles.empty}>
          <Ionicons name="search-outline" size={52} color={C.textMuted} />
          <Text style={[styles.emptyTitle, { color: C.text }]}>No results found</Text>
          <Text style={[styles.emptyDesc, { color: C.textSecondary }]}>
            No predictions match "{search}" — try a different search
          </Text>
          <TouchableOpacity
            style={[styles.emptyBtn, { backgroundColor: C.primaryLight }]}
            onPress={() => setSearch('')}
          >
            <Text style={[styles.emptyBtnText, { color: C.primary }]}>Clear Search</Text>
          </TouchableOpacity>
        </View>
      );
    }
    if (selectedLeague !== 'all') {
      return (
        <View style={styles.empty}>
          <Ionicons name="football-outline" size={52} color={C.textMuted} />
          <Text style={[styles.emptyTitle, { color: C.text }]}>No Predictions Yet</Text>
          <Text style={[styles.emptyDesc, { color: C.textSecondary }]}>
            No predictions available for this league right now. Try another league or check back later.
          </Text>
          <TouchableOpacity
            style={[styles.emptyBtn, { backgroundColor: C.primaryLight }]}
            onPress={() => setSelectedLeague('all')}
          >
            <Text style={[styles.emptyBtnText, { color: C.primary }]}>View All Leagues</Text>
          </TouchableOpacity>
        </View>
      );
    }
    return (
      <View style={styles.empty}>
        <Ionicons name="time-outline" size={52} color={C.textMuted} />
        <Text style={[styles.emptyTitle, { color: C.text }]}>No Predictions Yet</Text>
        <Text style={[styles.emptyDesc, { color: C.textSecondary }]}>
          Today's predictions haven't been generated yet. Pull down to refresh or check back soon.
        </Text>
        <TouchableOpacity
          style={[styles.emptyBtn, { backgroundColor: C.primaryLight }]}
          onPress={onRefresh}
        >
          <Ionicons name="refresh-outline" size={15} color={C.primary} />
          <Text style={[styles.emptyBtnText, { color: C.primary }]}>Refresh</Text>
        </TouchableOpacity>
      </View>
    );
  };

  return (
    <View style={[styles.container, { backgroundColor: C.background }]}>
      <Animated.View
        style={[
          styles.liveBanner,
          {
            backgroundColor: C.success,
            opacity: badgeFade,
            pointerEvents: newPredictionsBadge > 0 ? 'box-none' : 'none',
          },
          Platform.OS === 'web' && { top: 64 },
        ]}
      >
        <Animated.View style={{ transform: [{ scale: pulseAnim }] }}>
          <TouchableOpacity
            style={styles.liveBannerInner}
            onPress={() => { dismissBadge(); onRefresh(); }}
          >
            <Ionicons name="flash" size={14} color="#fff" />
            <Text style={styles.liveBannerText}>
              {newPredictionsBadge} new prediction{newPredictionsBadge !== 1 ? 's' : ''} — tap to refresh
            </Text>
          </TouchableOpacity>
        </Animated.View>
      </Animated.View>

      <View style={[styles.headerArea, { paddingTop: topInset + 8, backgroundColor: C.background }]}>
        <View style={styles.titleRow}>
          <Text style={[styles.title, { color: C.text }]}>Today's Predictions</Text>
          <View style={styles.titleRight}>
            <View style={[styles.aiBadge, { backgroundColor: C.primaryLight }]}>
              <Ionicons name="hardware-chip-outline" size={11} color={C.primary} />
              <Text style={[styles.aiText, { color: C.primary }]}>AI</Text>
            </View>
            <View style={[styles.tierBadge, { backgroundColor: C.primaryLight }]}>
              <Text style={[styles.tierText, { color: C.primary }]}>{userTier}</Text>
            </View>
          </View>
        </View>

        {isError && (
          <TouchableOpacity
            style={[styles.errorBanner, { backgroundColor: C.dangerLight, borderColor: C.danger }]}
            onPress={() => refetch()}
            activeOpacity={0.8}
          >
            <Ionicons name="wifi-outline" size={14} color={C.danger} />
            <Text style={[styles.errorText, { color: C.danger }]}>
              Couldn't load predictions — tap to retry
            </Text>
            <Ionicons name="refresh-outline" size={14} color={C.danger} />
          </TouchableOpacity>
        )}

        <View style={[styles.searchBar, { backgroundColor: C.inputBg, borderColor: C.border }]}>
          <Ionicons name="search-outline" size={18} color={C.placeholder} />
          <TextInput
            style={[styles.searchInput, { color: C.text }]}
            placeholder="Search predictions..."
            placeholderTextColor={C.placeholder}
            value={search}
            onChangeText={setSearch}
          />
          {search.length > 0 && (
            <TouchableOpacity onPress={() => setSearch('')}>
              <Ionicons name="close-circle" size={18} color={C.placeholder} />
            </TouchableOpacity>
          )}
        </View>

        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.leagueTabs}
          fadingEdgeLength={Platform.OS === 'android' ? 32 : undefined}
        >
          {LEAGUES.map(l => (
            <TouchableOpacity
              key={l.slug}
              style={[
                styles.leagueTab,
                {
                  backgroundColor: selectedLeague === l.slug ? C.primary : C.card,
                  borderColor: selectedLeague === l.slug ? C.primary : C.border,
                },
              ]}
              onPress={() => {
                setSelectedLeague(l.slug);
                Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
              }}
            >
              <Text
                style={[
                  styles.leagueTabText,
                  { color: selectedLeague === l.slug ? '#fff' : C.textSecondary },
                ]}
              >
                {l.name}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      </View>

      {isLoading ? (
        <FlatList
          data={[1, 2, 3, 4, 5]}
          keyExtractor={item => String(item)}
          contentContainerStyle={[styles.listContent, { paddingBottom: insets.bottom + 80 }]}
          renderItem={() => <SkeletonCard />}
          scrollEnabled={false}
        />
      ) : (
        <FlatList
          data={filtered}
          keyExtractor={item => item.id}
          contentContainerStyle={[
            styles.listContent,
            { paddingBottom: insets.bottom + (Platform.OS === 'web' ? 24 : 80) },
          ]}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor={C.primary}
              colors={[C.primary]}
            />
          }
          ListEmptyComponent={<EmptyState />}
          renderItem={({ item }) => (
            <PredictionCard
              prediction={item}
              locked={false}
              nextTier={nextTier}
              onPress={() => {
                setSelectedPrediction(item);
                Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
              }}
            />
          )}
        />
      )}

      <PredictionDetailModal
        prediction={selectedPrediction}
        visible={selectedPrediction !== null}
        onClose={() => setSelectedPrediction(null)}
      />

      <OnboardingModal
        visible={showOnboarding}
        onDone={() => setShowOnboarding(false)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  liveBanner: {
    position: 'absolute', top: 0, left: 0, right: 0, zIndex: 999,
    paddingTop: Platform.OS === 'ios' ? 44 : 0,
  },
  liveBannerInner: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 16, paddingVertical: 10,
  },
  liveBannerText: { color: '#fff', fontSize: 13, fontFamily: 'Inter_600SemiBold', flex: 1 },
  errorBanner: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    borderRadius: 10, borderWidth: 1, paddingHorizontal: 12, paddingVertical: 8,
    marginBottom: 10,
  },
  errorText: { flex: 1, fontSize: 13, fontFamily: 'Inter_400Regular' },
  headerArea: { paddingHorizontal: 16, paddingBottom: 8 },
  titleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  titleRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold' },
  aiBadge: { flexDirection: 'row', alignItems: 'center', gap: 3, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 20 },
  aiText: { fontSize: 11, fontFamily: 'Inter_700Bold' },
  tierBadge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 20 },
  tierText: { fontSize: 12, fontFamily: 'Inter_600SemiBold' },
  searchBar: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    borderRadius: 12, borderWidth: 1, paddingHorizontal: 12, paddingVertical: 10, marginBottom: 12,
  },
  searchInput: { flex: 1, fontSize: 15, fontFamily: 'Inter_400Regular' },
  leagueTabs: { gap: 8, paddingBottom: 4 },
  leagueTab: { paddingHorizontal: 14, paddingVertical: 7, borderRadius: 20, borderWidth: 1 },
  leagueTabText: { fontSize: 13, fontFamily: 'Inter_500Medium' },
  listContent: { paddingHorizontal: 16, paddingTop: 12 },
  empty: { alignItems: 'center', paddingTop: 60, gap: 12, paddingHorizontal: 32 },
  emptyTitle: { fontSize: 18, fontFamily: 'Inter_600SemiBold' },
  emptyDesc: { fontSize: 14, fontFamily: 'Inter_400Regular', textAlign: 'center', lineHeight: 21 },
  emptyBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 20, paddingVertical: 10, borderRadius: 20, marginTop: 4,
  },
  emptyBtnText: { fontSize: 14, fontFamily: 'Inter_600SemiBold' },
});
