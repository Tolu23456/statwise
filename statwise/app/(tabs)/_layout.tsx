import React from 'react';
import { Tabs } from 'expo-router';
import { Platform, View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Colors } from '@/constants/colors';
import { useTheme } from '@/context/ThemeContext';

const TAB_ITEMS = [
  { name: 'index', title: 'Predictions', icon: 'football-outline' as const },
  { name: 'insights', title: 'Insights', icon: 'analytics-outline' as const },
  { name: 'subscriptions', title: 'Plans', icon: 'diamond-outline' as const },
  { name: 'forum', title: 'Community', icon: 'chatbubbles-outline' as const },
  { name: 'profile', title: 'Profile', icon: 'person-outline' as const },
];

function WebTopNav({ state, navigation }: any) {
  const { scheme } = useTheme();
  const C = Colors[scheme];

  const visibleRoutes = state.routes.filter(
    (_: any, i: number) => i < TAB_ITEMS.length,
  );

  return (
    <View style={[styles.webNav, { backgroundColor: C.tabBar, borderBottomColor: C.tabBarBorder }]}>
      <View style={styles.brand}>
        <View style={[styles.brandIcon, { backgroundColor: C.primaryLight }]}>
          <Ionicons name="stats-chart" size={16} color={C.primary} />
        </View>
        <Text style={[styles.brandText, { color: C.text }]}>StatWise</Text>
      </View>

      <View style={styles.navTabs}>
        {visibleRoutes.map((route: any, index: number) => {
          const item = TAB_ITEMS[index];
          if (!item) return null;
          const isFocused = state.index === index;
          return (
            <TouchableOpacity
              key={route.key}
              style={[styles.navTab, isFocused && { borderBottomColor: C.primary, borderBottomWidth: 2 }]}
              onPress={() => navigation.navigate(route.name)}
              activeOpacity={0.75}
            >
              <Ionicons
                name={item.icon}
                size={17}
                color={isFocused ? C.primary : C.textMuted}
              />
              <Text style={[styles.navTabText, { color: isFocused ? C.primary : C.textMuted }]}>
                {item.title}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>
    </View>
  );
}

export default function TabLayout() {
  const { scheme } = useTheme();
  const C = Colors[scheme];

  const bottomTabBarStyle = {
    backgroundColor: C.tabBar,
    borderTopColor: C.tabBarBorder,
    borderTopWidth: 1,
    height: Platform.OS === 'web' ? 84 : undefined,
    paddingBottom: Platform.OS === 'web' ? 34 : undefined,
    display: Platform.OS === 'web' ? ('none' as const) : ('flex' as const),
  };

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: bottomTabBarStyle,
        tabBarActiveTintColor: C.primary,
        tabBarInactiveTintColor: C.textMuted,
        tabBarLabelStyle: { fontSize: 11, fontFamily: 'Inter_500Medium', marginBottom: 2 },
      }}
      tabBar={Platform.OS === 'web' ? (props) => <WebTopNav {...props} /> : undefined}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'Predictions',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="football-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="insights"
        options={{
          title: 'Insights',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="analytics-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="subscriptions"
        options={{
          title: 'Plans',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="diamond-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="forum"
        options={{
          title: 'Community',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="chatbubbles-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: 'Profile',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="person-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen name="insight-detail" options={{ href: null }} />
      <Tabs.Screen name="backtesting" options={{ href: null }} />
      <Tabs.Screen name="privacy-policy" options={{ href: null }} />
      <Tabs.Screen name="terms-of-service" options={{ href: null }} />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  webNav: {
    height: 64,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 20,
    borderBottomWidth: 1,
    gap: 0,
  },
  brand: { flexDirection: 'row', alignItems: 'center', gap: 8, marginRight: 32 },
  brandIcon: { width: 30, height: 30, borderRadius: 8, alignItems: 'center', justifyContent: 'center' },
  brandText: { fontSize: 18, fontFamily: 'Inter_700Bold', letterSpacing: -0.5 },
  navTabs: { flexDirection: 'row', flex: 1, gap: 0 },
  navTab: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 16, height: 64, borderBottomWidth: 0,
  },
  navTabText: { fontSize: 14, fontFamily: 'Inter_500Medium' },
});
