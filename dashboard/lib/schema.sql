create extension if not exists "pgcrypto";

create table destinations (
  id uuid primary key default gen_random_uuid(),
  url text not null unique,
  category text not null check (category in ('entertainment', 'soundy')),
  active boolean not null default false,
  created_at timestamptz default now()
);

create table locker_links (
  id uuid primary key default gen_random_uuid(),
  locker_url text not null,
  paste_rs_url text not null,
  created_at timestamptz default now()
);

create table run_logs (
  id uuid primary key default gen_random_uuid(),
  ts text,
  instance integer,
  device text,
  ip text,
  country text,
  mode text,
  url text,
  redirect text,
  success boolean,
  reason text,
  error text,
  video_reloads integer,
  bw_kb float,
  source text,
  created_at timestamptz default now()
);
