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
  if (msg.includes('not-null') || msg.includes('23502')) {
    return 'Profile issue. Run supabase-forum-fix.sql to fix this.';
  }
  if (msg.includes('network') || msg.includes('fetch') || msg.includes('Failed')) {
    return 'Connection error. Check your internet and try again.';
  }
  return msg.length > 120 ? msg.slice(0, 117) + '...' : msg;
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
    try {
      let { data, error } = await supabase
        .from('forum_messages')
        .select('*, user_profiles(username, display_name, avatar_url, current_tier)')
        .order('created_at', { ascending: true })
        .limit(100);

      if (error) {
        const msg: string = error?.message ?? '';
        if (msg.includes('user_profiles') || msg.includes('relation') || msg.includes('schema') || msg.includes('join')) {
          const res = await supabase
            .from('forum_messages')
            .select('*')
            .order('created_at', { ascending: true })
            .limit(100);
          if (res.error) {
            setLoadError(friendlyError(res.error));
            return;
          }
          setMessages((res.data ?? []) as ForumMessage[]);
        } else {
          setLoadError(friendlyError(error));
          return;
        }
      } else {
        setMessages((data ?? []) as ForumMessage[]);
      }
      setTimeout(() => flatListRef.current?.scrollToEnd({ animated: false }), 150);
    } catch (e: any) {
      setLoadError(friendlyError(e));
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    const timeout = setTimeout(() => setLoading(false), 8000);
    loadMessages()
      .catch(() => {})
      .finally(() => {
        clearTimeout(timeout);
        setLoading(false);
      });

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
    await loadMessages().catch(() => {});
    setTimeout(() => setRefreshing(false), 300);
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
        username: profile?.username ?? profile?.display_name ?? 'You',
        display_name: profile?.display_name ?? profile?.username ?? 'You',
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

  const renderItem = ({ item, index }: { item: ForumMessage; index: number }) => {
    const isMe = user?.id === item.user_id;
    const prev = messages[index - 1];
    const showHeader = !prev || prev.user_id !== item.user_id;
    const parsed = parseMessage(item.message);
    const profileData = (item as any).user_profiles || {};
    const displayName = profileData.display_name || profileData.username || item.username || 'User';
    const avatarUrl = profileData.avatar_url;
    const tier = profileData.current_tier || 'Free Tier';
    const isPremiumUser = PREMIUM_TIERS.includes(tier);

    return (
      <View style={[styles.msgRow, isMe && styles.msgRowMe]}>
        {!isMe && showHeader && (
          <View style={[styles.avatar, { backgroundColor: C.primaryLight }]}>
            {avatarUrl ? (
              <Image source={{ uri: avatarUrl }} style={styles.avatarImg} />
            ) : (
              <Text style={[styles.avatarText, { color: C.primary }]}>
                {displayName.slice(0, 2).toUpperCase()}
              </Text>
            )}
          </View>
        )}
        {!isMe && !showHeader && <View style={styles.avatarSpacer} />}
        <View style={[styles.bubbleCol, isMe && styles.bubbleColMe]}>
          {!isMe && showHeader && (
            <View style={styles.senderRow}>
              <Text style={[styles.senderName, { color: C.text }]} numberOfLines={1}>
                {displayName}
              </Text>
              {isPremiumUser && (
                <View style={[styles.tierPill, { backgroundColor: TierBadgeColors[tier]?.bg ?? C.primaryLight }]}>
                  <Text style={[styles.tierPillText, { color: TierBadgeColors[tier]?.text ?? C.primary }]}>
                    {tier === 'VVIP Tier' ? 'VVIP' : tier === 'VIP Tier' ? 'VIP' : 'PREM'}
                  </Text>
                </View>
              )}
            </View>
          )}
          <View style={[
            styles.bubble,
            isMe ? [styles.bubbleMe, { backgroundColor: C.primary }] : [styles.bubbleOther, { backgroundColor: C.card, borderColor: C.border }],
          ]}>
            {parsed.image && (
              <Image source={{ uri: parsed.image }} style={styles.mediaImage} resizeMode="cover" />
            )}
            {parsed.video && (
              <View style={[styles.mediaPlaceholder, { backgroundColor: C.inputBg }]}>
                <Ionicons name="play-circle" size={40} color="#fff" />
              </View>
            )}
            {parsed.text ? (
              <Text style={[styles.msgText, isMe && { color: '#fff' }]}>{parsed.text}</Text>
            ) : null}
            <Text style={[styles.timeText, isMe && { color: 'rgba(255,255,255,0.7)' }, { color: isMe ? 'rgba(255,255,255,0.7)' : C.textMuted }]}>
              {new Date(item.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </Text>
          </View>
        </View>
      </View>
    );
  };

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

      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={C.primary} />
          <Text style={[styles.loadingText, { color: C.textSecondary }]}>Loading messages...</Text>
        </View>
      ) : (
        <FlatList
          ref={flatListRef}
          data={messages}
          renderItem={renderItem}
          keyExtractor={item => item.id}
          contentContainerStyle={[styles.listContent, { paddingBottom: 12 }]}
          onContentSizeChange={() => flatListRef.current?.scrollToEnd({ animated: false })}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} colors={[C.primary]} />}
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

      <View style={[styles.inputBar, {
        paddingBottom: (Platform.OS === 'web' ? 16 : Math.max(insets.bottom, 10)),
        backgroundColor: C.background,
        borderTopColor: C.border,
      }]}>
        {sendError && (
          <View style={[styles.sendErrorBar, { backgroundColor: C.dangerLight, borderColor: C.danger }]}>
            <Ionicons name="alert-circle-outline" size={14} color={C.danger} />
            <Text style={[styles.sendErrorText, { color: C.danger }]} numberOfLines={3}>{sendError}</Text>
            <TouchableOpacity onPress={() => setSendError(null)}>
              <Ionicons name="close" size={14} color={C.danger} />
            </TouchableOpacity>
          </View>
        )}

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
              <TouchableOpacity style={styles.mediaBtn} onPress={() => pickAndUploadMedia('image')} activeOpacity={0.7}>
                <Ionicons name="image-outline" size={22} color={isPremium ? C.primary : C.textMuted} />
                {!isPremium && <View style={[styles.lockBadge, { backgroundColor: C.textMuted }]}><Ionicons name="lock-closed" size={7} color="#fff" /></View>}
              </TouchableOpacity>
              <TouchableOpacity style={styles.mediaBtn} onPress={() => pickAndUploadMedia('video')} activeOpacity={0.7}>
                <Ionicons name="videocam-outline" size={22} color={isPremium ? C.primary : C.textMuted} />
                {!isPremium && <View style={[styles.lockBadge, { backgroundColor: C.textMuted }]}><Ionicons name="lock-closed" size={7} color="#fff" /></View>}
              </TouchableOpacity>
            </View>

            <View style={[styles.inputContainer, {
              backgroundColor: C.inputBg,
              borderColor: sendError ? C.danger : C.border,
              alignItems: 'center',
              paddingRight: 6,
            }]}>
              <TextInput
                ref={inputRef}
                style={[
                  styles.textInput,
                  { color: C.text },
                  Platform.OS === 'web' ? ({ outlineStyle: 'none', boxShadow: 'none', borderWidth: 0 } as any) : null,
                ]}
                placeholder="Message…"
                placeholderTextColor={C.placeholder}
                value={input}
                onChangeText={v => { setInput(v); if (sendError) setSendError(null); }}
                multiline={Platform.OS !== 'web'}
                maxLength={1000}
                returnKeyType="send"
                blurOnSubmit={false}
                onSubmitEditing={Platform.OS === 'web' ? sendMessage : undefined}
                underlineColorAndroid="transparent"
                selectionColor={C.primary}
                textAlignVertical="center"
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
                  <Ionicons name="arrow-up" size={16} color={input.trim() && !isBusy ? '#fff' : C.textMuted} />
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
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingBottom: 10 },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold', flex: 1 },
  onlinePill: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 20 },
  dot: { width: 6, height: 6, borderRadius: 3 },
  onlineText: { fontSize: 11, fontFamily: 'Inter_600SemiBold' },
  errorBanner: { flexDirection: 'row', alignItems: 'center', gap: 8, marginHorizontal: 16, marginBottom: 10, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1 },
  errorBannerText: { flex: 1, fontSize: 12, fontFamily: 'Inter_400Regular' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  loadingText: { fontSize: 14, fontFamily: 'Inter_400Regular' },
  listContent: { paddingHorizontal: 16, paddingTop: 8, flexGrow: 1 },
  empty: { alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32, gap: 12, flex: 1 },
  emptyIconBg: { width: 80, height: 80, borderRadius: 40, alignItems: 'center', justifyContent: 'center' },
  emptyTitle: { fontSize: 18, fontFamily: 'Inter_600SemiBold' },
  emptyDesc: { fontSize: 14, fontFamily: 'Inter_400Regular', textAlign: 'center', lineHeight: 20 },
  msgRow: { flexDirection: 'row', marginBottom: 12, alignItems: 'flex-end' },
  msgRowMe: { justifyContent: 'flex-end' },
  avatar: { width: 32, height: 32, borderRadius: 16, alignItems: 'center', justifyContent: 'center', marginRight: 8 },
  avatarImg: { width: 32, height: 32, borderRadius: 16 },
  avatarText: { fontSize: 11, fontFamily: 'Inter_700Bold' },
  avatarSpacer: { width: 40 },
  bubbleCol: { maxWidth: '78%' },
  bubbleColMe: { alignItems: 'flex-end' },
  senderRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4, marginLeft: 4 },
  senderName: { fontSize: 12, fontFamily: 'Inter_600SemiBold', maxWidth: 160 },
  tierPill: { paddingHorizontal: 6, paddingVertical: 2, borderRadius: 6 },
  tierPillText: { fontSize: 9, fontFamily: 'Inter_700Bold', color: '#fff' },
  bubble: { borderRadius: 18, paddingHorizontal: 14, paddingVertical: 10, borderWidth: 1 },
  bubbleMe: { borderTopRightRadius: 4 },
  bubbleOther: { borderTopLeftRadius: 4 },
  mediaImage: { width: 220, height: 140, borderRadius: 12, marginBottom: 6 },
  mediaPlaceholder: { width: 220, height: 140, borderRadius: 12, marginBottom: 6, alignItems: 'center', justifyContent: 'center' },
  msgText: { fontSize: 14, fontFamily: 'Inter_400Regular', lineHeight: 20 },
  timeText: { fontSize: 10, fontFamily: 'Inter_400Regular', marginTop: 4, alignSelf: 'flex-end' },
  inputBar: { borderTopWidth: 1, paddingHorizontal: 12, paddingTop: 10 },
  sendErrorBar: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1 },
  sendErrorText: { flex: 1, fontSize: 11, fontFamily: 'Inter_400Regular' },
  loginPrompt: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 14, paddingVertical: 10, borderRadius: 12, borderWidth: 1 },
  loginPromptText: { fontSize: 14, fontFamily: 'Inter_400Regular' },
  inputRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 8 },
  mediaIcons: { flexDirection: 'row', gap: 2, alignItems: 'flex-end', paddingBottom: 6 },
  mediaBtn: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center', position: 'relative' },
  lockBadge: { position: 'absolute', bottom: 4, right: 4, width: 12, height: 12, borderRadius: 6, alignItems: 'center', justifyContent: 'center' },
  inputContainer: { flex: 1, flexDirection: 'row', alignItems: 'center', borderRadius: 22, borderWidth: 1, paddingLeft: 14, paddingRight: 4, paddingVertical: 4, minHeight: 44 },
  textInput: { flex: 1, fontSize: 15, fontFamily: 'Inter_400Regular', maxHeight: 100, paddingVertical: 6, paddingRight: 6 },
  sendBtn: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center', alignSelf: 'center', flexShrink: 0 },
});
