export interface CareerDomain {
  id: string;
  label: string;
  label_en: string;
  label_fr: string;
  occupations: number;
}

export interface CareerDomainList {
  domains: CareerDomain[];
  attribution: string;
}

export interface Occupation {
  uri: string;
  title: string;
  domain_id: string | null;
  description: string | null;
}

export interface OccupationList {
  occupations: Occupation[];
  attribution: string;
}

export interface DomainScore {
  id: string;
  label: string;
  probability: number;
}

export interface RoleFit {
  uri: string;
  title: string;
  domain_id: string | null;
  domain_label: string | null;
  fit: number;
  skills_you_have: string[];
  skills_to_build: string[];
}

export interface CareerInsights {
  has_cv: boolean;
  language: 'en' | 'fr';
  detected_domains: DomainScore[];
  target_domain: DomainScore | null;
  target_role: string | null;
  target_role_fit: RoleFit | null;
  suggested_roles: RoleFit[];
  available: boolean;
  attribution: string;
}

export interface AssistantStatus {
  enabled: boolean;
  personalised: boolean;
  reason: string | null;
  provider: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

export interface ChatHistory {
  messages: ChatMessage[];
}

export interface ChatReply {
  user_message: ChatMessage;
  reply: ChatMessage;
  tools_used: string[];
  personalised: boolean;
}
