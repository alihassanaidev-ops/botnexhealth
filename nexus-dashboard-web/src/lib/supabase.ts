import { createClient } from '@supabase/supabase-js'
import { secureStorage } from '@/lib/secure-storage'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

if (!supabaseUrl || !supabaseAnonKey) {
    throw new Error('Missing Supabase environment variables')
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
    auth: {
        storage: secureStorage,
        persistSession: true, // persists within in-memory storage only
        autoRefreshToken: true,
    },
})
