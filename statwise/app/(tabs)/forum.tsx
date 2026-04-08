import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, Text, StyleSheet, FlatList, TextInput, TouchableOpacity,
  ActivityIndicator, KeyboardAvoidingView, Platform, RefreshControl,
  Alert, Image,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import * as ImagePicker from 'expo-image-picker';
import { Colors, TierBadgeColors } from '@/constants/colors';
import { supabase, ForumMessage } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import { useTheme } from '@/context/ThemeContext';

const PREMIUM_TIERS = ['Premium Tier', 'VIP Tier', 'VVIP Tier'];

type ParsedMessage = { text?: string; image?: string; video?: string };

function parseMessage(raw: string): ParsedMessage {
  try {
    const p = JSON.parse(raw);
    if (p && typeof p === 'object' && ('text' in p || 'image' in p || 'video' in p)) return p;
  } catch {}
  return { text: raw };
}

export default function ForumScreen() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const { user, profile } = useAuth();

  const [messages, setMessages] = useState<ForumMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [mediaUploading, setMediaUploading] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const flatListRef = useRef<FlatList>(null);
  const inputRef = useRef<TextInput>(null);

  const topInset = Platform.OS === 'web' ? 0 : insets.top;
  const isPremium = PREMIUM_TIERS.includes(profile?.current_tier ?? '');

  const loadMessages = useCallback(async () => {
    setLoadError(null);
    const { data, error } = await supabase
      .from('forum_messages')
      .select('*, user_profiles(username, display_name, avatar_url, current_tier)')
      .order('created_at', { ascending: true })
      .limit(100);

    if (error) {
      setLoadError(friendlyError(error));
    } else {
      setMessages((data ?? []) as ForumMessage[]);
      setTimeout(() => flatListRef.current?.scrollToEnd({ animated: false }), 200);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    loadMessages().finally(() => setLoading(false));

    const channel = supabase
      .channel('forum_messages')
      .on('postgres_changes', {
        event: 'INSERT', schema: 'public', table: 'forum_messages',
      }, payload => {
        const msg = payload.new as ForumMessage;
        setMessages(prev => {
          if (prev.some(m => m.id === msg.id)) return prev;
          return [...prev, msg];
        });
        setTimeout(() => flatListRef.current?.scrollToEnd({ animated: true }), 100);
      })
      .subscribe();

    return () => { supabase.removeChannel(channel); };
  }, [loadMessages]);

  async function onRefresh() {
    setRefreshing(true);
    await loadMessages();
    setRefreshing(false);
  }

  const doSend = useCallback(async (content: string) => {
    if (!content.trim() || !user) return;
    setSendError(null);
    setSending(true);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);

    const optimisticId = `optimistic_${Date.now()}`;
    const optimistic: ForumMessage = {
      id: optimisticId,
      user_id: user.id,
      message: content,
      created_at: new Date().toISOString(),
      user_profiles: {
        username: profile?.username ?? '',
        display_name: profile?.display_name ?? 'You',
        avatar_url: profile?.avatar_url,
        current_tier: profile?.current_tier ?? 'Free Tier',
      },
    };

    setMessages(prev => [...prev, optimistic]);
    setTimeout(() => flatListRef.current?.scrollToEnd({ animated: true }), 100);

    const { error } = await supabase.rpc('send_forum_message', { p_message: content });

    if (error) {
      setMessages(prev => prev.filter(m => m.id !== optimisticId));
      setSendError(friendlyError(error));
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    }

    setSending(false);
  }, [user, profile]);

  async function sendMessage() {
    const text = input.trim();
    if (!text || sending || mediaUploading) return;
    setInput('');
    await doSend(text);
  }

  async function pickAndUploadMedia(type: 'image' | 'video') {
    if (!isPremium) {
      Alert.alert(
        'Premium Feature',
        `Sending ${type}s is available for Premium, VIP, and VVIP members. Upgrade your plan to unlock this feature.`,
        [{ text: 'OK' }],
      );
      return;
    }

    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: type === 'image'
        ? ImagePicker.MediaTypeOptions.Images
        : ImagePicker.MediaTypeOptions.Videos,
      quality: 0.8,
      allowsEditing: false,
    });

    if (result.canceled || !result.assets?.[0]) return;

    const asset = result.assets[0];
    const ext = (asset.uri.split('.').pop() ?? (type === 'image' ? 'jpg' : 'mp4')).toLowerCase();
    const path = `forum/${user!.id}/${Date.now()}.${ext}`;
    const mimeType = asset.mimeType ?? (type === 'image' ? `image/${ext}` : `video/${ext}`);

    setMediaUploading(true);
    setSendError(null);

    try {
      const resp = await fetch(asset.uri);
      const blob = await resp.blob();

      const { error: uploadError } = await supabase.storage
        .from('forum-media')
        .upload(path, blob, { contentType: mimeType });

      if (uploadError) throw uploadError;

      const { data: { publicUrl } } = supabase.storage.from('forum-media').getPublicUrl(path);

      const content = JSON.stringify(
        type === 'image'
          ? { image: publicUrl, text: input.trim() || '' }
          : { video: publicUrl, text: input.trim() || '' },
      );
      setInput('');
      await doSend(content);
    } catch (err: any) {
      setSendError(
        'Upload failed. Make sure a "forum-media" storage bucket exists in your Supabase dashboard (Storage → New bucket → "forum-media", set to Public).',
      );
    } finally {
      setMediaUploading(false);
    }
  }

  function friendlyError(error: any): string {
    const msg: string = error?.message ?? error?.toString() ?? 'Unknown error';
    if (msg.includes('row-level security') || msg.includes('42501') || error?.code === '42501') {
      return 'Permission denied (RLS). Please run the SQL fix in your Supabase dashboard.';
    }
    if (msg.includes('does not exist') || msg.includes('42P01') || error?.code === '42P01') {
      return 'Forum table missing. Run the setup SQL in your Supabase dashboard.';
    }
    if (msg.includes('send_forum_message') || msg.includes('function') || msg.includes('42883')) {
      return 'Forum function missing. Run supabase-forum-fix.sql in your Supabase SQL Editor.';
    }
    if (msg.includes('network') || msg.includes('fetch') || msg.includes('Failed')) {
      return 'Connection error. Check your internet and try again.';
    }
    return msg;
  }

  function getInitials(name?: string) {
    if (!name) return '?';
    return name.split(' ').map(n => n[0]).join('').toUpperCase().substring(0, 2);
  }

  function formatTime(ts: string) {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  const renderMessage = ({ item }: { item: ForumMessage }) => {
    const isMe = item.user_id === user?.id;
    const msgProfile = item.user_profiles;
    const tierColors = TierBadgeColors[msgProfile?.current_tier ?? 'Free Tier'];
    const isPending = item.id.startsWith('optimistic_');
    const parsed = parseMessage(item.message);

    return (
      <View style={[styles.msgRow, isMe && styles.msgRowMe]}>
        {!isMe && (
          <View style={[styles.avatar, { backgroundColor: C.primaryLight }]}>
            <Text style={[styles.avatarText, { color: C.primary }]}>
              {getInitials(msgProfile?.display_name ?? msgProfile?.username)}
            </Text>
          </View>
        )}
        <View style={[
          styles.bubble,
          isMe && styles.bubbleMe,
          {
            backgroundColor: isMe ? C.primary : C.card,
            borderColor: isMe ? C.primary : C.border,
            opacity: isPending ? 0.65 : 1,
          },
        ]}>
          {!isMe && (
            <View style={styles.nameRow}>
              <Text style={[styles.senderName, { color: C.text }]}>
                {msgProfile?.display_name ?? msgProfile?.username ?? 'User'}
              </Text>
              {msgProfile?.current_tier && msgProfile.current_tier !== 'Free Tier' && (
                <View style={[styles.tierPill, { backgroundColor: tierColors.bg }]}>
                  <Text style={[styles.tierPillText, { color: tierColors.text }]}>
                    {msgProfile.current_tier.replace(' Tier', '')}
                  </Text>
                </View>
              )}
            </View>
          )}

          {parsed.image && (
            <Image
              source={{ uri: parsed.image }}
              style={styles.msgImage}
              resizeMode="cover"
            />
          )}
          {parsed.video && (
            <View style={[styles.videoPlaceholder, { backgroundColor: C.border }]}>
              <Ionicons name="play-circle" size={40} color={isMe ? '#fff' : C.primary} />
              <Text style={[styles.videoLabel, { color: isMe ? 'rgba(255,255,255,0.8)' : C.textSecondary }]}>
                Video
              </Text>
            </View>
          )}
          {!!parsed.text && (
            <Text style={[
              styles.msgText,
              { color: isMe ? '#fff' : C.text },
              (parsed.image || parsed.video) && { marginTop: 6 },
            ]}>
              {parsed.text}
            </Text>
          )}

          <View style={styles.msgMeta}>
            {isPending && <Ionicons name="time-outline" size={11} color={isMe ? 'rgba(255,255,255,0.5)' : C.textMuted} />}
            <Text style={[styles.msgTime, { color: isMe ? 'rgba(255,255,255,0.6)' : C.textMuted }]}>
              {isPending ? 'Sending…' : formatTime(item.created_at)}
            </Text>
          </View>
        </View>
      </View>
    );
  };

  const showSetupGuide = loadError !== null && (
    loadError.toLowerCase().includes('rls') ||
    loadError.toLowerCase().includes('missing') ||
    loadError.toLowerCase().includes('function') ||
    loadError.toLowerCase().includes('does not exist')
  );

  const isBusy = sending || mediaUploading;

  return (
    <KeyboardAvoidingView
      style={[styles.container, { backgroundColor: C.background }]}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      keyboardVerticalOffset={0}
    >
      <View style={[styles.header, { paddingTop: topInset + 8 }]}>
        <Text style={[styles.title, { color: C.text }]}>Community</Text>
        <View style={[styles.onlinePill, { backgroundColor: C.successLight }]}>
          <View style={[styles.dot, { backgroundColor: C.success }]} />
          <Text style={[styles.onlineText, { color: C.success }]}>Live</Text>
        </View>
      </View>

      {loadError && (
        <TouchableOpacity
          style={[styles.errorBanner, { backgroundColor: C.dangerLight, borderColor: C.danger }]}
          onPress={onRefresh}
          activeOpacity={0.8}
        >
          <Ionicons name="warning-outline" size={15} color={C.danger} />
          <Text style={[styles.errorBannerText, { color: C.danger }]}>{loadError}</Text>
          <Ionicons name="refresh-outline" size={15} color={C.danger} />
        </TouchableOpacity>
      )}

      {showSetupGuide && (
        <View style={[styles.setupGuide, { backgroundColor: C.warningLight, borderColor: C.warning }]}>
          <Ionicons name="construct-outline" size={16} color={C.warning} />
          <View style={{ flex: 1 }}>
            <Text style={[styles.setupTitle, { color: C.warning }]}>One-time setup required</Text>
            <Text style={[styles.setupDesc, { color: C.textSecondary }]}>
              Run <Text style={{ fontFamily: 'Inter_600SemiBold' }}>statwise/supabase-forum-fix.sql</Text>
              {' '}in your Supabase dashboard → SQL Editor to enable forum posting.
            </Text>
          </View>
        </View>
      )}

      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={C.primary} />
          <Text style={[styles.loadingText, { color: C.textSecondary }]}>Loading messages…</Text>
        </View>
      ) : (
        <FlatList
          ref={flatListRef}
          data={messages}
          keyExtractor={item => item.id}
          contentContainerStyle={[
            styles.list,
            messages.length === 0 && styles.listEmpty,
          ]}
          renderItem={renderMessage}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} colors={[C.primary]} />
          }
          ListEmptyComponent={
            <View style={styles.empty}>
              <View style={[styles.emptyIconBg, { backgroundColor: C.primaryLight }]}>
                <Ionicons name="chatbubbles-outline" size={36} color={C.primary} />
              </View>
              <Text style={[styles.emptyTitle, { color: C.text }]}>No messages yet</Text>
              <Text style={[styles.emptyDesc, { color: C.textSecondary }]}>
                Be the first to start the conversation!
              </Text>
            </View>
          }
        />
      )}

      {sendError && (
        <View style={[styles.sendErrorBar, { backgroundColor: C.dangerLight, borderColor: C.danger }]}>
          <Ionicons name="alert-circle-outline" size={14} color={C.danger} />
          <Text style={[styles.sendErrorText, { color: C.danger }]} numberOfLines={3}>{sendError}</Text>
          <TouchableOpacity onPress={() => setSendError(null)}>
            <Ionicons name="close" size={16} color={C.danger} />
          </TouchableOpacity>
        </View>
      )}

      <View style={[styles.inputBar, {
        backgroundColor: C.card,
        borderTopColor: C.border,
        paddingBottom: insets.bottom > 0 ? insets.bottom : (Platform.OS === 'web' ? 12 : 10),
      }]}>
        {!user ? (
          <View style={[styles.loginPrompt, { backgroundColor: C.inputBg, borderColor: C.border }]}>
            <Ionicons name="lock-closed-outline" size={16} color={C.textSecondary} />
            <Text style={[styles.loginPromptText, { color: C.textSecondary }]}>
              Sign in to join the conversation
            </Text>
          </View>
        ) : (
          <View style={styles.inputRow}>
            <View style={styles.mediaIcons}>
              <TouchableOpacity
                style={styles.mediaBtn}
                onPress={() => pickAndUploadMedia('image')}
                activeOpacity={0.7}
              >
                <Ionicons
                  name="image-outline"
                  size={22}
                  color={isPremium ? C.primary : C.textMuted}
                />
                {!isPremium && (
                  <View style={[styles.lockBadge, { backgroundColor: C.textMuted }]}>
                    <Ionicons name="lock-closed" size={7} color="#fff" />
                  </View>
                )}
              </TouchableOpacity>

              <TouchableOpacity
                style={styles.mediaBtn}
                onPress={() => pickAndUploadMedia('video')}
                activeOpacity={0.7}
              >
                <Ionicons
                  name="videocam-outline"
                  size={22}
                  color={isPremium ? C.primary : C.textMuted}
                />
                {!isPremium && (
                  <View style={[styles.lockBadge, { backgroundColor: C.textMuted }]}>
                    <Ionicons name="lock-closed" size={7} color="#fff" />
                  </View>
                )}
              </TouchableOpacity>
            </View>

            <View style={[styles.inputContainer, {
              backgroundColor: C.inputBg,
              borderColor: sendError ? C.danger : C.border,
            }]}>
              <TextInput
                ref={inputRef}
                style={[styles.textInput, { color: C.text }]}
                placeholder="Message…"
                placeholderTextColor={C.placeholder}
                value={input}
                onChangeText={v => { setInput(v); if (sendError) setSendError(null); }}
                multiline={Platform.OS !== 'web'}
                maxLength={1000}
                returnKeyType="send"
                blurOnSubmit={false}
                onSubmitEditing={Platform.OS === 'web' ? sendMessage : undefined}
              />

              {(mediaUploading) ? (
                <View style={[styles.sendBtn, { backgroundColor: C.primary }]}>
                  <ActivityIndicator color="#fff" size="small" />
                </View>
              ) : (
                <TouchableOpacity
                  style={[
                    styles.sendBtn,
                    { backgroundColor: input.trim() && !isBusy ? C.primary : C.border },
                  ]}
                  onPress={sendMessage}
                  disabled={!input.trim() || isBusy}
                  activeOpacity={0.85}
                >
                  <Ionicons name="arrow-up" size={16} color="#fff" />
                </TouchableOpacity>
              )}
            </View>
          </View>
        )}
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: 16, paddingBottom: 10,
  },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold', flex: 1 },
  onlinePill: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 10, paddingVertical: 4, borderRadius: 20,
  },
  dot: { width: 6, height: 6, borderRadius: 3 },
  onlineText: { fontSize: 12, fontFamily: 'Inter_600SemiBold' },
  errorBanner: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 8,
    marginHorizontal: 12, borderRadius: 10, borderWidth: 1, padding: 10, marginBottom: 4,
  },
  errorBannerText: { flex: 1, fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 17 },
  setupGuide: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 10,
    marginHorizontal: 12, borderRadius: 12, borderWidth: 1, padding: 12, marginBottom: 4,
  },
  setupTitle: { fontSize: 13, fontFamily: 'Inter_600SemiBold', marginBottom: 3 },
  setupDesc: { fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 17 },
  list: { paddingHorizontal: 12, paddingTop: 8, paddingBottom: 8, gap: 4 },
  listEmpty: { flexGrow: 1, justifyContent: 'center' },
  msgRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 8, marginBottom: 6 },
  msgRowMe: { flexDirection: 'row-reverse' },
  avatar: {
    width: 34, height: 34, borderRadius: 17,
    alignItems: 'center', justifyContent: 'center', flexShrink: 0,
  },
  avatarText: { fontSize: 12, fontFamily: 'Inter_700Bold' },
  bubble: {
    maxWidth: '75%', borderRadius: 18, borderWidth: 1, padding: 10,
    borderBottomLeftRadius: 4,
  },
  bubbleMe: { borderBottomLeftRadius: 18, borderBottomRightRadius: 4 },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 },
  senderName: { fontSize: 13, fontFamily: 'Inter_600SemiBold' },
  tierPill: { paddingHorizontal: 6, paddingVertical: 2, borderRadius: 10 },
  tierPillText: { fontSize: 10, fontFamily: 'Inter_600SemiBold' },
  msgImage: { width: 200, height: 160, borderRadius: 10 },
  videoPlaceholder: {
    width: 200, height: 120, borderRadius: 10,
    alignItems: 'center', justifyContent: 'center', gap: 6,
  },
  videoLabel: { fontSize: 12, fontFamily: 'Inter_500Medium' },
  msgText: { fontSize: 15, fontFamily: 'Inter_400Regular', lineHeight: 20 },
  msgMeta: { flexDirection: 'row', alignItems: 'center', gap: 3, marginTop: 4, alignSelf: 'flex-end' },
  msgTime: { fontSize: 11, fontFamily: 'Inter_400Regular' },
  sendErrorBar: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    marginHorizontal: 12, borderRadius: 10, borderWidth: 1, padding: 8, marginBottom: 4,
  },
  sendErrorText: { flex: 1, fontSize: 12, fontFamily: 'Inter_400Regular' },
  inputBar: {
    paddingHorizontal: 12, paddingTop: 10, borderTopWidth: 1,
  },
  loginPrompt: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    borderRadius: 22, borderWidth: 1, paddingHorizontal: 14, paddingVertical: 12,
  },
  loginPromptText: { fontSize: 14, fontFamily: 'Inter_400Regular' },
  inputRow: {
    flexDirection: 'row', alignItems: 'flex-end', gap: 8,
  },
  mediaIcons: {
    flexDirection: 'row', gap: 2,
    alignItems: 'flex-end', paddingBottom: 6,
  },
  mediaBtn: {
    width: 36, height: 36, alignItems: 'center', justifyContent: 'center',
    position: 'relative',
  },
  lockBadge: {
    position: 'absolute', bottom: 4, right: 4,
    width: 12, height: 12, borderRadius: 6,
    alignItems: 'center', justifyContent: 'center',
  },
  inputContainer: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'flex-end',
    borderRadius: 22,
    borderWidth: 1,
    paddingLeft: 14,
    paddingRight: 4,
    paddingVertical: 4,
    minHeight: 44,
  },
  textInput: {
    flex: 1,
    fontSize: 15,
    fontFamily: 'Inter_400Regular',
    maxHeight: 100,
    paddingVertical: 6,
    paddingRight: 6,
  },
  sendBtn: {
    width: 34, height: 34, borderRadius: 17,
    alignItems: 'center', justifyContent: 'center',
    marginBottom: 1,
    flexShrink: 0,
  },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  loadingText: { fontSize: 14, fontFamily: 'Inter_400Regular' },
  empty: { alignItems: 'center', paddingHorizontal: 32, gap: 12 },
  emptyIconBg: { width: 80, height: 80, borderRadius: 40, alignItems: 'center', justifyContent: 'center' },
  emptyTitle: { fontSize: 18, fontFamily: 'Inter_600SemiBold' },
  emptyDesc: { fontSize: 14, fontFamily: 'Inter_400Regular', textAlign: 'center', lineHeight: 20 },
});
