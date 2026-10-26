import React, { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity, TextInput,
  RefreshControl, ScrollView, Platform, ActivityIndicator,
  Animated, AppState,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import Ionicons from '@expo/vector-icons/Ionicons';
import * as Haptics from 'expo-haptics';
import { Colors } from '@/constants/colors';
import { supabase, Prediction } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import { useTheme } from '@/context/ThemeContext';
import { PredictionCard } from '@/components/PredictionCard';
import { PredictionDetailModal } from '@/components/PredictionDetailModal';
import { SkeletonCard } from '@/components/SkeletonCard';
import { OnboardingModal, shouldShowOnboarding } from '@/components/OnboardingModal';
import { BookingCodeModal } from '@/components/BookingCodeModal';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { formatDayLabel, formatNumber, timeAgo } from '@/lib/format';
import { pollIntervalMs, kickoffMs } from '@/lib/polling';
import { currentNetworkState, subscribeNetwork, type NetworkState } from '@/lib/network';

/** Which upstream feed produced a fixture. Newer rows store this in stats;
 *  older ones only carry it in the canonical match_id namespace. */
function predictionSource(p: Prediction): 'espn' | 'football-data.org' | 'unknown' {
  const stored = p.stats?.source;
  if (stored === 'espn' || stored === 'football-data.org') return stored;
  if (p.match_id?.startsWith('espn_')) return 'espn';
  if (p.match_id?.startsWith('fdorg_')) return 'football-data.org';
  return 'unknown';
}

const LEAGUES = [
  { slug: 'all', name: 'All' },
  { slug: 'premier-league', name: 'Premier League' },
  { slug: 'la-liga', name: 'La Liga' },
  { slug: 'bundesliga', name: 'Bundesliga' },
  { slug: 'serie-a', name: 'Serie A' },
  { slug: 'ligue1', name: 'Ligue 1' },
  { slug: 'champions-league', name: 'UCL' },
  { slug: 'europa-league', name: 'Europa' },
  { slug: 'conference-league', name: 'Conference' },
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

type ListRow =
  | { kind: 'header'; key: string; label: string; count: number }
  | { kind: 'prediction'; key: string; prediction: Prediction };

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
  const router = useRouter();

  const [selectedLeague, setSelectedLeague] = useState('all');
  const [selectedSource, setSelectedSource] = useState<'all' | 'espn' | 'football-data.org'>('all');
  const [search, setSearch] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [newPredictionsBadge, setNewPredictionsBadge] = useState(0);
  const [selectedPrediction, setSelectedPrediction] = useState<Prediction | null>(null);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [showModelInfo, setShowModelInfo] = useState(false);
  const [showBookingCode, setShowBookingCode] = useState(false);

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

  // Plan 2.1 — visibility detection: pause all interval polling while the
  // app/tab is backgrounded (AppState is backed by visibilitychange on web).
  const [appActive, setAppActive] = useState(
    () => AppState.currentState !== 'background' && AppState.currentState !== 'inactive',
  );
  useEffect(() => {
    const sub = AppState.addEventListener('change', state => {
      setAppActive(state === 'active');
    });
    return () => sub?.remove();
  }, []);

  // Plan 2.1 — network awareness: offline stops polling entirely, slow
  // (2g/3g-class) connections halve the polling frequency.
  const [network, setNetwork] = useState<NetworkState>(() => currentNetworkState());

  // Urgency ticker: recompute the poll tier as kickoffs pass (cheap, no I/O).
  // Without it a fixture going from "quiet" to "live" would only be noticed
  // on the next refetch (up to 5 minutes late).
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!appActive) return;
    const id = setInterval(() => setNowMs(Date.now()), 30 * 1000);
    return () => clearInterval(id);
  }, [appActive]);

  const { data: predictions = [], isLoading, isError, refetch, isFetching, dataUpdatedAt } = useQuery<Prediction[]>({
    queryKey: ['predictions', userTier],
    queryFn: async ({ signal }) => {
      try {
        const today = new Date().toISOString().split('T')[0];
        // Use abort controller compatible query
        const query = supabase
          .from('predictions')
          .select('*')
          .gte('match_date', today)
          .in('tier', allowedTiers)
          .order('kickoff_time', { ascending: true })
          .limit(userLimit === Infinity ? 500 : userLimit);
        
        const { data, error } = await query;
        if (error) {
          console.error('Predictions fetch error:', error);
          // Don't throw on network errors to avoid crashes
          if (error.message?.includes('fetch') || error.message?.includes('network')) {
            return [];
          }
          throw error;
        }
        return data ?? [];
      } catch (e: any) {
        // Ignore abort errors to prevent noisy logs
        if (e?.name === 'AbortError' || e?.message?.includes('abort') || e?.message?.includes('Lock broken') || e?.message?.includes('fetch')) {
          return [];
        }
        console.error('Predictions query error:', e);
        throw e;
      }
    },
    // Plan 2.1 — tiered polling: live 15s / starting-soon 60s / quiet 300s,
    // `false` (zero requests) when hidden or offline, doubled on slow links.
    // Function form reads the freshest rows straight off the query.
    refetchInterval: query => {
      const rows: Prediction[] = query.state.data ?? [];
      const kickoffs: number[] = [];
      for (const row of rows) {
        const t = kickoffMs(row);
        if (t !== null) kickoffs.push(t);
      }
      return pollIntervalMs({
        appActive,
        online: network.online,
        slow: network.slow,
        kickoffs,
        now: nowMs,
      });
    },
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    refetchOnMount: true,
    staleTime: 30 * 1000,
    retry: (failureCount, error: any) => {
      if (error?.name === 'AbortError' || error?.message?.includes('abort') || error?.message?.includes('Lock broken') || error?.message?.includes('fetch')) {
        return false;
      }
      return failureCount < 1;
    },
    retryDelay: 1000,
  });

  // Plan 2.1 — subscribe to network changes; on reconnect, refresh right
  // away instead of waiting out the current poll tier.
  useEffect(() => {
    let wasOnline = currentNetworkState().online;
    return subscribeNetwork(state => {
      setNetwork(prev =>
        prev.online === state.online && prev.slow === state.slow ? prev : state,
      );
      if (state.online && !wasOnline) {
        refetch().catch(() => {});
      }
      wasOnline = state.online;
    });
  }, [refetch]);

  // How many predictions exist for this tier overall — used to tell free users
  // how many more they could unlock.
  const { data: totalAvailable } = useQuery<number>({
    queryKey: ['predictions-total', userTier],
    queryFn: async () => {
      try {
        const today = new Date().toISOString().split('T')[0];
        const { count, error } = await supabase
          .from('predictions')
          .select('id', { count: 'exact', head: true })
          .gte('match_date', today)
          .in('tier', allowedTiers);
        if (error) {
          if (error.message?.includes('abort') || error.message?.includes('Lock broken')) return 0;
          throw error;
        }
        return count ?? 0;
      } catch (e: any) {
        if (e?.name === 'AbortError' || e?.message?.includes('abort') || e?.message?.includes('Lock broken')) {
          return 0;
        }
        throw e;
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: 1,
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
    await refetch().catch(() => {});
    setNewPredictionsBadge(0);
    setTimeout(() => setRefreshing(false), 300);
  }, [refetch]);

  const filtered = useMemo(() => {
    let list = predictions;
    if (selectedLeague !== 'all') {
      list = list.filter(p => p.league_slug === selectedLeague);
    }
    if (selectedSource !== 'all') {
      list = list.filter(p => predictionSource(p) === selectedSource);
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
  }, [predictions, selectedLeague, selectedSource, search]);

  /** Match counts per source tab. Old rows predate the stored flag, so the
   *  match_id namespace is used as the fallback. */
  const sourceCounts = useMemo(() => {
    const counts: Record<string, number> = { all: predictions.length, espn: 0, 'football-data.org': 0 };
    for (const p of predictions) {
      const src = predictionSource(p);
      if (src in counts) counts[src] += 1;
    }
    return counts;
  }, [predictions]);

  /** Match counts per league tab (based on the unfiltered day's list). */
  const leagueCounts = useMemo(() => {
    const counts: Record<string, number> = { all: predictions.length };
    for (const p of predictions) {
      counts[p.league_slug] = (counts[p.league_slug] ?? 0) + 1;
    }
    return counts;
  }, [predictions]);

  /** Flatten predictions into day headers + rows so the feed reads chronologically. */
  const rows = useMemo<ListRow[]>(() => {
    // First pass: how many matches fall on each calendar day.
    const counts: Record<string, number> = {};
    filtered.forEach(p => {
      const label = formatDayLabel(p.kickoff_time || p.match_date);
      counts[label] = (counts[label] ?? 0) + 1;
    });

    // Second pass: emit a header whenever the day changes.
    const out: ListRow[] = [];
    let currentLabel = '';
    filtered.forEach((p, idx) => {
      const label = formatDayLabel(p.kickoff_time || p.match_date);
      if (label !== currentLabel) {
        currentLabel = label;
        out.push({
          kind: 'header',
          key: `hdr-${label}-${idx}`,
          label: label || 'Upcoming',
          count: counts[label] ?? 0,
        });
      }
      out.push({ kind: 'prediction', key: p.id, prediction: p });
    });
    return out;
  }, [filtered]);

  const topInset = Platform.OS === 'web' ? 0 : insets.top;

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
          disabled={refreshing || isFetching}
          activeOpacity={0.8}
        >
          {(refreshing || isFetching) ? (
            <ActivityIndicator size="small" color={C.primary} />
          ) : (
            <Ionicons name="refresh-outline" size={15} color={C.primary} />
          )}
          <Text style={[styles.emptyBtnText, { color: C.primary }]}>
            {(refreshing || isFetching) ? 'Refreshing...' : 'Refresh'}
          </Text>
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
          <View style={styles.titleLeft}>
            <Text style={[styles.title, { color: C.text }]}>Predictions</Text>
            {predictions.length > 0 && (
              <Text style={[styles.subtitle, { color: C.textMuted }]}>
                {formatNumber(predictions.length)} match{predictions.length === 1 ? '' : 'es'}
                {userLimit !== Infinity && totalAvailable ? ` of ${formatNumber(totalAvailable)}` : ''}
                {dataUpdatedAt ? ` · ${timeAgo(new Date(dataUpdatedAt).toISOString())}` : ''}
              </Text>
            )}
          </View>
          <View style={styles.titleRight}>
            <TouchableOpacity
              style={[styles.bookingBadge, { backgroundColor: C.successLight }]}
              onPress={() => setShowBookingCode(true)}
              activeOpacity={0.8}
              hitSlop={6}
            >
              <Ionicons name="ticket-outline" size={12} color={C.success} />
              <Text style={[styles.bookingText, { color: C.success }]}>Booking Code</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.aiBadge, { backgroundColor: C.primaryLight }]}
              onPress={() => setShowModelInfo(true)}
              activeOpacity={0.8}
              hitSlop={6}
            >
              <Ionicons name="hardware-chip-outline" size={12} color={C.primary} />
              <Text style={[styles.aiText, { color: C.primary }]}>Pico</Text>
              <Ionicons name="information-circle-outline" size={12} color={C.primary} />
            </TouchableOpacity>
            <View style={[styles.tierBadge, { backgroundColor: C.primaryLight }]}>
              <Text style={[styles.tierText, { color: C.primary }]}>{userTier.replace(' Tier', '')}</Text>
            </View>
          </View>
        </View>

        {isError && (
          <TouchableOpacity
            style={[styles.errorBanner, { backgroundColor: C.dangerLight, borderColor: C.danger }]}
            onPress={() => refetch()}
            activeOpacity={0.8}
            disabled={isFetching}
          >
            <Ionicons name="wifi-outline" size={14} color={C.danger} />
            <Text style={[styles.errorText, { color: C.danger }]}>
              {isFetching ? 'Retrying...' : "Couldn't load predictions — tap to retry"}
            </Text>
            {isFetching ? (
              <ActivityIndicator size="small" color={C.danger} />
            ) : (
              <Ionicons name="refresh-outline" size={14} color={C.danger} />
            )}
          </TouchableOpacity>
        )}

        {/* Free tier upsell — shows how many picks are hidden behind an upgrade */}
        {userTier === 'Free Tier' && (totalAvailable ?? 0) > predictions.length && (
          <TouchableOpacity
            style={[styles.upsellBanner, { backgroundColor: C.goldLight, borderColor: C.gold }]}
            onPress={() => router.push('/(tabs)/subscriptions')}
            activeOpacity={0.85}
          >
            <View style={[styles.upsellIcon, { backgroundColor: C.gold }]}>
              <Ionicons name="lock-open" size={14} color="#fff" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[styles.upsellTitle, { color: C.text }]}>
                {formatNumber((totalAvailable ?? 0) - predictions.length)} more picks available
              </Text>
              <Text style={[styles.upsellSub, { color: C.textSecondary }]}>
                Upgrade to Premium for 25 matches a day
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={16} color={C.gold} />
          </TouchableOpacity>
        )}

        <View style={[styles.searchBar, { backgroundColor: C.inputBg, borderColor: C.border }]}>
          <Ionicons name="search-outline" size={18} color={C.placeholder} />
          <TextInput
            style={[
              styles.searchInput,
              { color: C.text },
              Platform.OS === 'web' ? ({ outlineStyle: 'none', boxShadow: 'none', borderWidth: 0 } as any) : null,
            ]}
            placeholder="Search team, league or pick..."
            placeholderTextColor={C.placeholder}
            value={search}
            onChangeText={setSearch}
            underlineColorAndroid="transparent"
            selectionColor={C.primary}
          />
          {search.length > 0 && (
            <TouchableOpacity onPress={() => setSearch('')} hitSlop={8}>
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
          {LEAGUES.map(l => {
            const count = leagueCounts[l.slug] ?? 0;
            if (l.slug !== 'all' && count === 0) return null;
            const active = selectedLeague === l.slug;
            return (
              <TouchableOpacity
                key={l.slug}
                style={[
                  styles.leagueTab,
                  {
                    backgroundColor: active ? C.primary : C.card,
                    borderColor: active ? C.primary : C.border,
                  },
                ]}
                onPress={() => {
                  setSelectedLeague(l.slug);
                  Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                }}
                activeOpacity={0.85}
              >
                <Text
                  style={[
                    styles.leagueTabText,
                    { color: active ? '#fff' : C.textSecondary },
                  ]}
                >
                  {l.name}
                </Text>
                <View
                  style={[
                    styles.leagueCount,
                    { backgroundColor: active ? 'rgba(255,255,255,0.25)' : C.inputBg },
                  ]}
                >
                  <Text
                    style={[
                      styles.leagueCountText,
                      { color: active ? '#fff' : C.textMuted },
                    ]}
                  >
                    {count}
                  </Text>
                </View>
              </TouchableOpacity>
            );
          })}
        </ScrollView>

        {/* Source filter: ESPN vs football-data.org. Only shown when both
            feeds actually contributed fixtures for the current day. */}
        {sourceCounts.espn > 0 && sourceCounts['football-data.org'] > 0 && (
          <View style={styles.sourceRow}>
            {([
              { id: 'all' as const, label: 'All sources' },
              { id: 'football-data.org' as const, label: 'football-data.org' },
              { id: 'espn' as const, label: 'ESPN' },
            ]).map(s => {
              const active = selectedSource === s.id;
              const count = s.id === 'all' ? sourceCounts.all : sourceCounts[s.id];
              return (
                <TouchableOpacity
                  key={s.id}
                  onPress={() => {
                    setSelectedSource(s.id);
                    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                  }}
                  style={[
                    styles.sourceChip,
                    {
                      backgroundColor: active ? C.primaryLight : C.card,
                      borderColor: active ? C.primary : C.border,
                    },
                  ]}
                  activeOpacity={0.85}
                >
                  <Text style={[styles.sourceChipText, { color: active ? C.primary : C.textMuted }]}>
                    {s.label} {count}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        )}
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
          data={rows}
          keyExtractor={item => item.key}
          contentContainerStyle={[
            styles.listContent,
            { paddingBottom: insets.bottom + (Platform.OS === 'web' ? 24 : 80) },
          ]}
          refreshControl={
            <RefreshControl
              refreshing={refreshing || isFetching}
              onRefresh={onRefresh}
              tintColor={C.primary}
              colors={[C.primary]}
            />
          }
          ListEmptyComponent={<EmptyState />}
          ListFooterComponent={
            filtered.length > 0 ? (
              <Text style={[styles.footerNote, { color: C.textMuted }]}>
                {formatNumber(filtered.length)} prediction{filtered.length === 1 ? '' : 's'} shown
                {search.trim() ? ' for your search' : ''}
                {' · '}Pico can still be wrong. Play responsibly.
              </Text>
            ) : null
          }
          renderItem={({ item }) => {
            if (item.kind === 'header') {
              return (
                <View style={styles.dayHeader}>
                  <View style={[styles.dayRule, { backgroundColor: C.border }]} />
                  <Text style={[styles.dayLabel, { color: C.text }]}>{item.label}</Text>
                  <View style={[styles.dayCountPill, { backgroundColor: C.inputBg }]}>
                    <Text style={[styles.dayCountText, { color: C.textSecondary }]}>{item.count}</Text>
                  </View>
                  <View style={[styles.dayRule, { backgroundColor: C.border }]} />
                </View>
              );
            }
            return (
              <PredictionCard
                prediction={item.prediction}
                locked={false}
                nextTier={nextTier}
                onPress={() => {
                  setSelectedPrediction(item.prediction);
                  Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                }}
              />
            );
          }}
        />
      )}

      <PredictionDetailModal
        prediction={selectedPrediction}
        visible={selectedPrediction !== null}
        onClose={() => setSelectedPrediction(null)}
      />

      <BookingCodeModal
        visible={showBookingCode}
        onClose={() => setShowBookingCode(false)}
        predictions={predictions}
      />

      <OnboardingModal
        visible={showOnboarding}
        onDone={() => setShowOnboarding(false)}
      />

      {showModelInfo && (
        <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.65)', justifyContent: 'center', alignItems: 'center', padding: 20 }}>
          <View style={{ backgroundColor: C.card, borderRadius: 16, maxWidth: 440, width: '100%', maxHeight: '85%', borderWidth: 1, borderColor: C.border }}>
            <ScrollView contentContainerStyle={{ padding: 20 }} showsVerticalScrollIndicator={false}>
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <Ionicons name="hardware-chip-outline" size={22} color={C.primary} />
                  <Text style={{ fontSize: 18, fontFamily: 'Inter_700Bold', color: C.text }}>Pico — StatWise AI</Text>
                </View>
                <TouchableOpacity onPress={() => setShowModelInfo(false)} hitSlop={10}>
                  <Ionicons name="close" size={22} color={C.textMuted} />
                </TouchableOpacity>
              </View>

              <Text style={{ fontSize: 13, fontFamily: 'Inter_400Regular', color: C.textSecondary, lineHeight: 20, marginBottom: 12 }}>
                Pico is a LightGBM gradient-boosting ensemble trained on 300,000+ historical football matches across 100+ leagues, using 161 engineered features: team form, head-to-head history, squad value, Elo ratings, goal expectancy, weather and market odds.
              </Text>

              <Text style={{ fontSize: 13, fontFamily: 'Inter_400Regular', color: C.textSecondary, lineHeight: 20, marginBottom: 12 }}>
                Probabilities are isotonic-calibrated on an out-of-time holdout and then blended with de-vigged bookmaker odds, with the blend weight learned per league.
              </Text>

              <View style={{ backgroundColor: C.background, borderWidth: 1, borderColor: C.border, borderRadius: 12, padding: 14, marginBottom: 12 }}>
                <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: C.text, marginBottom: 6 }}>Certainty & the AI Pick badge</Text>
                <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: C.textSecondary, lineHeight: 18 }}>
                  Pico measures how sure it really is using three signals: probability entropy (how spread out the outcomes are), agreement between model heads, and the margin over the second-most-likely result. When those are combined, you get the Certainty score.
                </Text>
                <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: C.textSecondary, lineHeight: 18, marginTop: 8 }}>
                  Matches where Pico is genuinely certain are marked with an <Text style={{ color: C.success, fontWeight: '700' }}>AI Pick</Text> badge and reserved for the top tier. Low-certainty matches are pushed down to the free tier, and Pico will say so when it is not sure.
                </Text>
              </View>

              <View style={{ backgroundColor: C.warningLight, borderWidth: 1, borderColor: C.warning, borderRadius: 12, padding: 14, marginBottom: 12 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <Ionicons name="alert-circle" size={15} color={C.warning} />
                  <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: C.text }}>Accuracy disclaimer</Text>
                </View>
                <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: C.textSecondary, lineHeight: 18 }}>
                  Football is inherently difficult to predict and no model can be 100% accurate. Even our highest-certainty picks will lose sometimes. Treat every prediction as probabilistic information, not a guarantee. Always gamble responsibly and only with money you can afford to lose.
                </Text>
              </View>

              <TouchableOpacity style={{ backgroundColor: C.primary, paddingVertical: 12, borderRadius: 12, alignItems: 'center' }} onPress={() => setShowModelInfo(false)}>
                <Text style={{ color: '#fff', fontFamily: 'Inter_600SemiBold' }}>Got it</Text>
              </TouchableOpacity>
            </ScrollView>
          </View>
        </View>
      )}
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
  titleLeft: { flex: 1, marginRight: 10 },
  titleRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold' },
  subtitle: { fontSize: 12, marginTop: 2 },
  aiBadge: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 9, paddingVertical: 5, borderRadius: 20 },
  aiText: { fontSize: 11, fontFamily: 'Inter_700Bold' },
  tierBadge: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: 20 },
  tierText: { fontSize: 12, fontFamily: 'Inter_600SemiBold' },
  upsellBanner: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    borderRadius: 12, borderWidth: 1, paddingHorizontal: 12, paddingVertical: 10, marginBottom: 12,
  },
  upsellIcon: { width: 28, height: 28, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  upsellTitle: { fontSize: 13, fontFamily: 'Inter_600SemiBold' },
  upsellSub: { fontSize: 12, marginTop: 1 },
  searchBar: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    borderRadius: 12, borderWidth: 1, paddingHorizontal: 12, paddingVertical: 10, marginBottom: 12,
  },
  searchInput: { flex: 1, fontSize: 15, fontFamily: 'Inter_400Regular' },
  leagueTabs: { gap: 8, paddingBottom: 4 },
  leagueTab: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 20, borderWidth: 1,
  },
  leagueTabText: { fontSize: 13, fontFamily: 'Inter_500Medium' },
  leagueCount: {
    minWidth: 20, paddingHorizontal: 5, paddingVertical: 1,
    borderRadius: 10, alignItems: 'center', justifyContent: 'center',
  },
  leagueCountText: { fontSize: 11, fontWeight: '700' },
  sourceRow: {
    flexDirection: 'row',
    gap: 6,
    marginTop: 8,
    flexWrap: 'wrap',
  },
  sourceChip: {
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 14,
    borderWidth: 1,
  },
  sourceChipText: { fontSize: 11, fontFamily: 'Inter_500Medium' },
  listContent: { paddingHorizontal: 16, paddingTop: 12 },
  dayHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 10, marginTop: 4 },
  dayRule: { flex: 1, height: 1 },
  dayLabel: { fontSize: 13, fontWeight: '700', letterSpacing: 0.3 },
  dayCountPill: { paddingHorizontal: 7, paddingVertical: 2, borderRadius: 10 },
  dayCountText: { fontSize: 11, fontWeight: '600' },
  footerNote: { fontSize: 11, textAlign: 'center', marginTop: 4, lineHeight: 16 },
  empty: { alignItems: 'center', paddingTop: 60, gap: 12, paddingHorizontal: 32 },
  emptyTitle: { fontSize: 18, fontFamily: 'Inter_600SemiBold' },
  emptyDesc: { fontSize: 14, fontFamily: 'Inter_400Regular', textAlign: 'center', lineHeight: 21 },

  bookingBadge: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 9, paddingVertical: 5, borderRadius: 20 },
  bookingText: { fontSize: 11, fontFamily: 'Inter_700Bold' },
  emptyBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 20, paddingVertical: 10, borderRadius: 20, marginTop: 4,
  },
  emptyBtnText: { fontSize: 14, fontFamily: 'Inter_600SemiBold' },
});
