#![no_std]
#![allow(non_snake_case)]

use soroban_sdk::{
    contract, contracterror, contractimpl, contracttype, log, symbol_short, token, Address, Env,
};

// Storage rent constants
const DAY_IN_LEDGERS: u32 = 17_280;
const INSTANCE_BUMP_AMOUNT: u32 = 30 * DAY_IN_LEDGERS;
const INSTANCE_LIFETIME_THRESHOLD: u32 = INSTANCE_BUMP_AMOUNT - DAY_IN_LEDGERS;

const BOUNTY_BUMP_AMOUNT: u32 = 60 * DAY_IN_LEDGERS;
const BOUNTY_LIFETIME_THRESHOLD: u32 = BOUNTY_BUMP_AMOUNT - DAY_IN_LEDGERS;

// Time parameters
const MIN_DEADLINE_DURATION: u64 = 86_400;
const REVIEW_PERIOD: u64 = 7 * 86_400;
const REVISION_PERIOD: u64 = 3 * 86_400;

// Types

#[contracttype]
#[derive(Clone, Debug, PartialEq)]
pub enum BountyStatus {
    Posted,
    Claimed,
    Submitted,
    Approved,
    Paid,
    Expired,
    Resolved,
}

#[contracttype]
#[derive(Clone, Debug)]
pub struct BountyEscrow {
    pub bounty_id: u64,
    pub poster: Address,
    pub contributor: Option<Address>,
    pub token: Address,
    pub amount: i128,
    pub status: BountyStatus,
    pub deadline: u64,
    pub review_deadline: Option<u64>,
    pub created_at: u64,
}

#[contracttype]
pub enum DataKey {
    Admin,
    PendingAdmin,
    BountyCounter,
    Bounty(u64),
}

// Errors

#[contracterror]
#[derive(Copy, Clone, Debug, PartialEq)]
pub enum EscrowError {
    NotFound = 1,
    InvalidStatus = 2,
    Unauthorized = 3,
    NotExpired = 4,
    InvalidAmount = 5,
    InvalidDeadline = 6,
    SelfClaim = 7,
    DeadlinePassed = 8,
    ReviewPeriodActive = 9,
    ReviewPeriodExpired = 10,
    InvalidToken = 11,
}

// Contract

#[contract]
pub struct EscrowContract;

#[contractimpl]
impl EscrowContract {
    // Admin functions

    pub fn initialize(env: Env, admin: Address) {
        if env.storage().instance().has(&DataKey::Admin) {
            panic!("already initialized");
        }
        env.storage().instance().set(&DataKey::Admin, &admin);
        env.storage().instance().set(&DataKey::BountyCounter, &0u64);

        env.storage()
            .instance()
            .extend_ttl(INSTANCE_LIFETIME_THRESHOLD, INSTANCE_BUMP_AMOUNT);
        log!(&env, "Contract initialized with admin: {:?}", admin);
    }

    pub fn transfer_admin(env: Env, new_admin: Address) -> Result<(), EscrowError> {
        let admin: Address = env
            .storage()
            .instance()
            .get(&DataKey::Admin)
            .expect("not initialized");
        admin.require_auth();

        env.storage()
            .instance()
            .set(&DataKey::PendingAdmin, &new_admin);
        env.storage()
            .instance()
            .extend_ttl(INSTANCE_LIFETIME_THRESHOLD, INSTANCE_BUMP_AMOUNT);

        env.events().publish(
            (symbol_short!("admin"), symbol_short!("transfer")),
            (admin, new_admin),
        );

        Ok(())
    }

    pub fn accept_admin(env: Env) -> Result<(), EscrowError> {
        let pending: Address = env
            .storage()
            .instance()
            .get(&DataKey::PendingAdmin)
            .ok_or(EscrowError::Unauthorized)?;
        pending.require_auth();

        env.storage().instance().set(&DataKey::Admin, &pending);
        env.storage().instance().remove(&DataKey::PendingAdmin);
        env.storage()
            .instance()
            .extend_ttl(INSTANCE_LIFETIME_THRESHOLD, INSTANCE_BUMP_AMOUNT);

        env.events()
            .publish((symbol_short!("admin"), symbol_short!("accepted")), pending);

        Ok(())
    }

    // Bounty management

    pub fn create_bounty(
        env: Env,
        poster: Address,
        token: Address,
        amount: i128,
        deadline: u64,
    ) -> Result<u64, EscrowError> {
        poster.require_auth();

        if amount <= 0 {
            return Err(EscrowError::InvalidAmount);
        }

        let contract_address = env.current_contract_address();
        if token == contract_address {
            return Err(EscrowError::InvalidToken);
        }

        let current_time = env.ledger().timestamp();
        if deadline < current_time + MIN_DEADLINE_DURATION {
            return Err(EscrowError::InvalidDeadline);
        }

        token::Client::new(&env, &token).transfer(&poster, &contract_address, &amount);

        let mut counter: u64 = env
            .storage()
            .instance()
            .get(&DataKey::BountyCounter)
            .unwrap_or(0);
        counter += 1;

        let bounty = BountyEscrow {
            bounty_id: counter,
            poster: poster.clone(),
            contributor: None,
            token: token.clone(),
            amount,
            status: BountyStatus::Posted,
            deadline,
            review_deadline: None,
            created_at: current_time,
        };

        let key = DataKey::Bounty(counter);
        env.storage().persistent().set(&key, &bounty);
        env.storage()
            .persistent()
            .extend_ttl(&key, BOUNTY_LIFETIME_THRESHOLD, BOUNTY_BUMP_AMOUNT);

        env.storage()
            .instance()
            .set(&DataKey::BountyCounter, &counter);
        env.storage()
            .instance()
            .extend_ttl(INSTANCE_LIFETIME_THRESHOLD, INSTANCE_BUMP_AMOUNT);

        env.events().publish(
            (symbol_short!("bounty"), symbol_short!("created")),
            (counter, poster, amount),
        );

        Ok(counter)
    }

    pub fn claim_bounty(env: Env, bounty_id: u64, contributor: Address) -> Result<(), EscrowError> {
        contributor.require_auth();

        let key = DataKey::Bounty(bounty_id);
        let mut bounty: BountyEscrow = env
            .storage()
            .persistent()
            .get(&key)
            .ok_or(EscrowError::NotFound)?;

        if bounty.status != BountyStatus::Posted {
            return Err(EscrowError::InvalidStatus);
        }
        if bounty.poster == contributor {
            return Err(EscrowError::SelfClaim);
        }
        if env.ledger().timestamp() >= bounty.deadline {
            return Err(EscrowError::DeadlinePassed);
        }

        bounty.contributor = Some(contributor.clone());
        bounty.status = BountyStatus::Claimed;

        env.storage().persistent().set(&key, &bounty);
        env.storage()
            .persistent()
            .extend_ttl(&key, BOUNTY_LIFETIME_THRESHOLD, BOUNTY_BUMP_AMOUNT);

        env.events().publish(
            (symbol_short!("bounty"), symbol_short!("claimed")),
            (bounty_id, contributor),
        );

        Ok(())
    }

    pub fn submit_work(env: Env, bounty_id: u64, contributor: Address) -> Result<(), EscrowError> {
        contributor.require_auth();

        let key = DataKey::Bounty(bounty_id);
        let mut bounty: BountyEscrow = env
            .storage()
            .persistent()
            .get(&key)
            .ok_or(EscrowError::NotFound)?;

        if bounty.status != BountyStatus::Claimed {
            return Err(EscrowError::InvalidStatus);
        }
        if bounty.contributor.as_ref() != Some(&contributor) {
            return Err(EscrowError::Unauthorized);
        }
        let current_time = env.ledger().timestamp();
        if current_time >= bounty.deadline {
            return Err(EscrowError::DeadlinePassed);
        }

        bounty.status = BountyStatus::Submitted;
        bounty.review_deadline = Some(current_time + REVIEW_PERIOD);

        env.storage().persistent().set(&key, &bounty);
        env.storage()
            .persistent()
            .extend_ttl(&key, BOUNTY_LIFETIME_THRESHOLD, BOUNTY_BUMP_AMOUNT);

        env.events().publish(
            (symbol_short!("bounty"), symbol_short!("submitted")),
            (bounty_id, contributor),
        );

        Ok(())
    }

    pub fn reject_work(env: Env, bounty_id: u64, poster: Address) -> Result<(), EscrowError> {
        poster.require_auth();

        let key = DataKey::Bounty(bounty_id);
        let mut bounty: BountyEscrow = env
            .storage()
            .persistent()
            .get(&key)
            .ok_or(EscrowError::NotFound)?;

        if bounty.status != BountyStatus::Submitted {
            return Err(EscrowError::InvalidStatus);
        }
        if bounty.poster != poster {
            return Err(EscrowError::Unauthorized);
        }

        let current_time = env.ledger().timestamp();
        let review_deadline = bounty.review_deadline.ok_or(EscrowError::InvalidStatus)?;

        if current_time > review_deadline {
            return Err(EscrowError::ReviewPeriodExpired);
        }

        bounty.status = BountyStatus::Claimed;
        bounty.review_deadline = None;

        if current_time >= bounty.deadline {
            bounty.deadline = current_time + REVISION_PERIOD;
        }

        env.storage().persistent().set(&key, &bounty);
        env.storage()
            .persistent()
            .extend_ttl(&key, BOUNTY_LIFETIME_THRESHOLD, BOUNTY_BUMP_AMOUNT);

        env.events().publish(
            (symbol_short!("bounty"), symbol_short!("rejected")),
            (bounty_id, bounty.deadline),
        );

        Ok(())
    }

    // Approval & Settlement

    /// Poster approves submitted work.
    pub fn approve_work(env: Env, bounty_id: u64, poster: Address) -> Result<(), EscrowError> {
        poster.require_auth();

        let key = DataKey::Bounty(bounty_id);
        let mut bounty: BountyEscrow = env
            .storage()
            .persistent()
            .get(&key)
            .ok_or(EscrowError::NotFound)?;

        if bounty.status != BountyStatus::Submitted {
            return Err(EscrowError::InvalidStatus);
        }
        if bounty.poster != poster {
            return Err(EscrowError::Unauthorized);
        }

        let contributor = bounty.contributor.clone().ok_or(EscrowError::NotFound)?;

        bounty.status = BountyStatus::Approved;
        bounty.review_deadline = None;

        env.storage().persistent().set(&key, &bounty);
        env.storage()
            .persistent()
            .extend_ttl(&key, BOUNTY_LIFETIME_THRESHOLD, BOUNTY_BUMP_AMOUNT);

        env.events().publish(
            (symbol_short!("bounty"), symbol_short!("approved")),
            (bounty_id, contributor),
        );

        Ok(())
    }

    /// Release payment to the contributor.
    pub fn claim_payment(env: Env, bounty_id: u64) -> Result<(), EscrowError> {
        let key = DataKey::Bounty(bounty_id);
        let mut bounty: BountyEscrow = env
            .storage()
            .persistent()
            .get(&key)
            .ok_or(EscrowError::NotFound)?;

        let is_autopay = match bounty.status {
            BountyStatus::Approved => false,
            BountyStatus::Submitted => {
                let current_time = env.ledger().timestamp();
                let review_deadline = bounty.review_deadline.ok_or(EscrowError::InvalidStatus)?;
                if current_time <= review_deadline {
                    return Err(EscrowError::ReviewPeriodActive);
                }
                true
            }
            _ => return Err(EscrowError::InvalidStatus),
        };

        let contributor = bounty.contributor.clone().ok_or(EscrowError::NotFound)?;
        let contract_address = env.current_contract_address();
        token::Client::new(&env, &bounty.token).transfer(
            &contract_address,
            &contributor,
            &bounty.amount,
        );

        bounty.status = BountyStatus::Paid;
        bounty.review_deadline = None;

        env.storage().persistent().set(&key, &bounty);
        env.storage()
            .persistent()
            .extend_ttl(&key, BOUNTY_LIFETIME_THRESHOLD, BOUNTY_BUMP_AMOUNT);

        if is_autopay {
            env.events().publish(
                (symbol_short!("bounty"), symbol_short!("autopaid")),
                (bounty_id, contributor, bounty.amount),
            );
        } else {
            env.events().publish(
                (symbol_short!("bounty"), symbol_short!("paid")),
                (bounty_id, contributor, bounty.amount),
            );
        }

        Ok(())
    }

    /// Approve work and release payment.
    pub fn approve_and_pay(env: Env, bounty_id: u64, poster: Address) -> Result<(), EscrowError> {
        poster.require_auth();

        let key = DataKey::Bounty(bounty_id);
        let mut bounty: BountyEscrow = env
            .storage()
            .persistent()
            .get(&key)
            .ok_or(EscrowError::NotFound)?;

        if bounty.status != BountyStatus::Submitted {
            return Err(EscrowError::InvalidStatus);
        }
        if bounty.poster != poster {
            return Err(EscrowError::Unauthorized);
        }

        let contributor = bounty.contributor.clone().ok_or(EscrowError::NotFound)?;

        env.events().publish(
            (symbol_short!("bounty"), symbol_short!("approved")),
            (bounty_id, contributor.clone()),
        );

        let contract_address = env.current_contract_address();
        token::Client::new(&env, &bounty.token).transfer(
            &contract_address,
            &contributor,
            &bounty.amount,
        );

        bounty.status = BountyStatus::Paid;
        bounty.review_deadline = None;

        env.storage().persistent().set(&key, &bounty);
        env.storage()
            .persistent()
            .extend_ttl(&key, BOUNTY_LIFETIME_THRESHOLD, BOUNTY_BUMP_AMOUNT);

        env.events().publish(
            (symbol_short!("bounty"), symbol_short!("paid")),
            (bounty_id, contributor, bounty.amount),
        );

        Ok(())
    }

    // Expiry & Disputes

    pub fn expire_and_refund(env: Env, bounty_id: u64) -> Result<(), EscrowError> {
        let key = DataKey::Bounty(bounty_id);
        let mut bounty: BountyEscrow = env
            .storage()
            .persistent()
            .get(&key)
            .ok_or(EscrowError::NotFound)?;

        if bounty.status != BountyStatus::Posted && bounty.status != BountyStatus::Claimed {
            return Err(EscrowError::InvalidStatus);
        }

        if env.ledger().timestamp() < bounty.deadline {
            return Err(EscrowError::NotExpired);
        }

        let contract_address = env.current_contract_address();
        token::Client::new(&env, &bounty.token).transfer(
            &contract_address,
            &bounty.poster,
            &bounty.amount,
        );

        bounty.status = BountyStatus::Expired;
        bounty.review_deadline = None;

        env.storage().persistent().set(&key, &bounty);
        env.storage()
            .persistent()
            .extend_ttl(&key, BOUNTY_LIFETIME_THRESHOLD, BOUNTY_BUMP_AMOUNT);

        env.events().publish(
            (symbol_short!("bounty"), symbol_short!("expired")),
            (bounty_id, bounty.poster.clone(), bounty.amount),
        );

        Ok(())
    }

    /// Admin resolution for disputed submissions.
    pub fn resolve_dispute(
        env: Env,
        bounty_id: u64,
        poster_amount: i128,
        contributor_amount: i128,
    ) -> Result<(), EscrowError> {
        let admin: Address = env
            .storage()
            .instance()
            .get(&DataKey::Admin)
            .expect("not initialized");
        admin.require_auth();

        let key = DataKey::Bounty(bounty_id);
        let mut bounty: BountyEscrow = env
            .storage()
            .persistent()
            .get(&key)
            .ok_or(EscrowError::NotFound)?;

        if bounty.status != BountyStatus::Submitted {
            return Err(EscrowError::InvalidStatus);
        }

        if poster_amount < 0 || contributor_amount < 0 {
            return Err(EscrowError::InvalidAmount);
        }

        if poster_amount + contributor_amount != bounty.amount {
            return Err(EscrowError::InvalidAmount);
        }

        let contributor = if contributor_amount > 0 {
            Some(bounty.contributor.clone().ok_or(EscrowError::NotFound)?)
        } else {
            None
        };

        bounty.status = BountyStatus::Resolved;
        bounty.review_deadline = None;

        env.storage().persistent().set(&key, &bounty);
        env.storage()
            .persistent()
            .extend_ttl(&key, BOUNTY_LIFETIME_THRESHOLD, BOUNTY_BUMP_AMOUNT);

        let contract_address = env.current_contract_address();
        let token_client = token::Client::new(&env, &bounty.token);

        if poster_amount > 0 {
            token_client.transfer(&contract_address, &bounty.poster, &poster_amount);
        }
        if let Some(c) = contributor {
            token_client.transfer(&contract_address, &c, &contributor_amount);
        }

        env.events().publish(
            (symbol_short!("bounty"), symbol_short!("resolved")),
            (bounty_id, poster_amount, contributor_amount),
        );

        Ok(())
    }

    // Query Functions

    pub fn get_bounty(env: Env, bounty_id: u64) -> Result<BountyEscrow, EscrowError> {
        let key = DataKey::Bounty(bounty_id);
        let bounty = env
            .storage()
            .persistent()
            .get(&key)
            .ok_or(EscrowError::NotFound)?;
        env.storage()
            .persistent()
            .extend_ttl(&key, BOUNTY_LIFETIME_THRESHOLD, BOUNTY_BUMP_AMOUNT);
        Ok(bounty)
    }

    pub fn get_bounty_count(env: Env) -> u64 {
        env.storage()
            .instance()
            .extend_ttl(INSTANCE_LIFETIME_THRESHOLD, INSTANCE_BUMP_AMOUNT);
        env.storage()
            .instance()
            .get(&DataKey::BountyCounter)
            .unwrap_or(0)
    }

    pub fn get_admin(env: Env) -> Address {
        env.storage()
            .instance()
            .get(&DataKey::Admin)
            .expect("not initialized")
    }

    pub fn get_pending_admin(env: Env) -> Option<Address> {
        env.storage()
            .instance()
            .extend_ttl(INSTANCE_LIFETIME_THRESHOLD, INSTANCE_BUMP_AMOUNT);
        env.storage().instance().get(&DataKey::PendingAdmin)
    }
}

// Tests

#[cfg(test)]
mod tests {
    use super::*;
    use soroban_sdk::{
        testutils::{Address as _, Ledger},
        token::{StellarAssetClient, TokenClient},
        Env,
    };

    fn setup_env() -> (Env, Address, Address, Address, EscrowContractClient<'static>) {
        let env = Env::default();
        env.mock_all_auths();

        let admin = Address::generate(&env);
        let poster = Address::generate(&env);
        let contributor = Address::generate(&env);

        let contract_id = env.register_contract(None, EscrowContract);
        let client = EscrowContractClient::new(&env, &contract_id);

        client.initialize(&admin);

        (env, poster, contributor, contract_id, client)
    }

    fn setup_token(env: &Env, _admin: &Address, poster: &Address, amount: i128) -> Address {
        let token_admin = Address::generate(env);
        let token_contract = env.register_stellar_asset_contract_v2(token_admin);
        let token_address = token_contract.address();

        let sac_client = StellarAssetClient::new(env, &token_address);
        sac_client.mint(poster, &amount);

        token_address
    }

    fn set_ledger_timestamp(env: &Env, timestamp: u64) {
        let mut ledger = env.ledger().get();
        ledger.timestamp = timestamp;
        env.ledger().set(ledger);
    }

    #[test]
    fn test_full_bounty_lifecycle() {
        let (env, poster, contributor, _contract_id, client) = setup_env();

        let token_address = setup_token(&env, &Address::generate(&env), &poster, 1000);
        let deadline = env.ledger().timestamp() + 86400; // 24h

        let bounty_id = client.create_bounty(&poster, &token_address, &500, &deadline);
        assert_eq!(bounty_id, 1);

        let bounty = client.get_bounty(&bounty_id);
        assert_eq!(bounty.status, BountyStatus::Posted);
        assert_eq!(bounty.amount, 500);

        client.claim_bounty(&bounty_id, &contributor);
        let bounty = client.get_bounty(&bounty_id);
        assert_eq!(bounty.status, BountyStatus::Claimed);
        assert_eq!(bounty.contributor, Some(contributor.clone()));

        client.submit_work(&bounty_id, &contributor);
        let bounty = client.get_bounty(&bounty_id);
        assert_eq!(bounty.status, BountyStatus::Submitted);

        client.approve_and_pay(&bounty_id, &poster);
        let bounty = client.get_bounty(&bounty_id);
        assert_eq!(bounty.status, BountyStatus::Paid);

        let token_client = TokenClient::new(&env, &token_address);
        assert_eq!(token_client.balance(&contributor), 500);
    }

    #[test]
    fn test_expire_and_refund() {
        let (env, poster, _contributor, _contract_id, client) = setup_env();

        let token_address = setup_token(&env, &Address::generate(&env), &poster, 1000);
        let deadline = env.ledger().timestamp() + 86401;

        let bounty_id = client.create_bounty(&poster, &token_address, &300, &deadline);

        set_ledger_timestamp(&env, deadline + 1);

        client.expire_and_refund(&bounty_id);
        let bounty = client.get_bounty(&bounty_id);
        assert_eq!(bounty.status, BountyStatus::Expired);

        let token_client = TokenClient::new(&env, &token_address);
        assert_eq!(token_client.balance(&poster), 1000);
    }

    #[test]
    fn test_cannot_self_claim() {
        let (env, poster, _contributor, _contract_id, client) = setup_env();

        let token_address = setup_token(&env, &Address::generate(&env), &poster, 1000);
        let deadline = env.ledger().timestamp() + 86400;

        let bounty_id = client.create_bounty(&poster, &token_address, &500, &deadline);

        let res = client.try_claim_bounty(&bounty_id, &poster);
        assert!(res.is_err());
    }

    #[test]
    fn test_cannot_expire_before_deadline() {
        let (env, poster, _contributor, _contract_id, client) = setup_env();

        let token_address = setup_token(&env, &Address::generate(&env), &poster, 1000);
        let deadline = env.ledger().timestamp() + 86400;

        let bounty_id = client.create_bounty(&poster, &token_address, &500, &deadline);

        let res = client.try_expire_and_refund(&bounty_id);
        assert!(res.is_err());
    }

    #[test]
    fn test_cannot_submit_without_claim() {
        let (env, poster, contributor, _contract_id, client) = setup_env();

        let token_address = setup_token(&env, &Address::generate(&env), &poster, 1000);
        let deadline = env.ledger().timestamp() + 86400;

        let bounty_id = client.create_bounty(&poster, &token_address, &500, &deadline);

        let res = client.try_submit_work(&bounty_id, &contributor);
        assert!(res.is_err());
    }

    #[test]
    fn test_cannot_approve_without_submission() {
        let (env, poster, contributor, _contract_id, client) = setup_env();

        let token_address = setup_token(&env, &Address::generate(&env), &poster, 1000);
        let deadline = env.ledger().timestamp() + 86400;

        let bounty_id = client.create_bounty(&poster, &token_address, &500, &deadline);
        client.claim_bounty(&bounty_id, &contributor);

        let res = client.try_approve_and_pay(&bounty_id, &poster);
        assert!(res.is_err());
    }

    #[test]
    fn test_multiple_bounties() {
        let (env, poster, _contributor, _contract_id, client) = setup_env();

        let token_address = setup_token(&env, &Address::generate(&env), &poster, 10000);
        let deadline = env.ledger().timestamp() + 86400;

        let id1 = client.create_bounty(&poster, &token_address, &100, &deadline);
        let id2 = client.create_bounty(&poster, &token_address, &200, &deadline);
        let id3 = client.create_bounty(&poster, &token_address, &300, &deadline);

        assert_eq!(id1, 1);
        assert_eq!(id2, 2);
        assert_eq!(id3, 3);
        assert_eq!(client.get_bounty_count(), 3);
    }
}
