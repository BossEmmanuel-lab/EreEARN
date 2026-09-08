# **ÉréEARN MVP – Project Brief**

## **Overview**

**ÉréEARN** is a bounty marketplace built on Stellar where projects can post funded tasks and contributors can discover, claim, complete, and get paid for them.

The core of the product is a **Soroban-powered escrow system on Stellar Testnet**.

Instead of contributors trusting that a project will pay them after completing work, the bounty reward is locked on-chain before work begins.

### **Sprint Goal**

**Within 14 days**, build a functional MVP where:

* Projects can post funded bounties  
* Rewards are locked in a Soroban escrow contract  
* Contributors can discover and claim bounties  
* Contributors can submit completed work  
* Posters can review submissions  
* Approved work triggers payment to the contributor  
* All important actions are verifiable on Stellar Testnet

# **Core Features**

## **1\. Bounty Escrow Smart Contract**

This is the core infrastructure of ÉréEARN.

When a project posts a bounty, the reward should be locked in the Soroban escrow contract.

The bounty lifecycle should follow:

**POSTED → CLAIMED → SUBMITTED → APPROVED → PAID**

If the bounty expires without an accepted submission:

**EXPIRED → Funds returned to the poster**

### **Supported Assets**

The MVP should support:

* XLM  
* USDC

The contract should generate verifiable on-chain events for important bounty actions.

# **2\. Contributor Marketplace**

Contributors should be able to browse available bounties.

Each bounty should display:

* Bounty title  
* Description  
* Required skill  
* Reward amount  
* Asset  
* Deadline  
* Escrow status

### **Skill Categories**

The marketplace should support filtering by:

* Development  
* Design  
* Writing  
* Video Creation  
* Project Management  
* Community

A key feature should be the **escrow status badge**, showing contributors that the reward has been funded and locked on-chain.

# **3\. Wallet Connection**

Contributors and bounty posters should be able to connect their Stellar Testnet wallets.

The wallet should be used for:

* Funding bounties  
* Claiming bounties  
* Receiving payments  
* Approving required transactions

All transactions should happen on **Stellar Testnet**.

# **4\. Claim a Bounty**

A contributor should be able to:

1. Browse a bounty  
2. View its details  
3. Connect their wallet  
4. Claim the bounty

Once claimed, the bounty status should update accordingly.

# **5\. Contributor Dashboard**

The contributor dashboard should show:

* Active bounty claims  
* Submitted work  
* Completed bounties  
* Payments received

Each completed payment should include a visible Stellar Testnet transaction hash or explorer link.

# **6\. Submit Work**

After completing a bounty, the contributor should be able to submit their work for review.

The submission flow should allow the contributor to provide the required submission details or links.

The bounty status should then change to:

**SUBMITTED**

# **7\. Poster Dashboard**

Projects and organizations should have a dashboard where they can:

* Create new bounties  
* View active bounties  
* View claimed bounties  
* Review contributor submissions  
* Approve submissions  
* Request revisions

# **8\. Create a Bounty**

The poster should be able to create a bounty by entering:

* Bounty title  
* Task description  
* Required skill type  
* Reward amount  
* Payout asset  
* Deadline

When the bounty is posted, the reward should be locked into the escrow contract.

The bounty should only become publicly available once the funding is successfully confirmed.

# **9\. Submission Review & Payment**

When a contributor submits work, the poster should be able to:

* Review the submission  
* Leave notes  
* Request revisions  
* Approve the submission

When approved:

1. The bounty status changes to **APPROVED**  
2. The escrow contract releases the reward  
3. Funds are sent to the contributor's wallet  
4. The bounty status changes to **PAID**  
5. The transaction can be verified on Stellar Testnet

# **Key Screens for Design**

The design team should focus on:

1. Landing / Homepage  
2. Bounty Marketplace  
3. Bounty Details Page  
4. Wallet Connection Flow  
5. Contributor Dashboard  
6. Claim Bounty Flow  
7. Submission Flow  
8. Poster Dashboard  
9. Create Bounty Flow  
10. Submission Review Panel  
11. Payment / Transaction Status

The product should feel like a **simple and modern job/bounty marketplace**.

The blockchain and escrow infrastructure should work in the background without making the experience unnecessarily technical.

# **Technical Requirements**

### **Blockchain**

* Stellar Testnet  
* Soroban smart contract

### **Escrow**

The smart contract should handle:

* Bounty funding  
* Fund locking  
* Bounty state changes  
* Payment release after approval  
* Refund to poster after expiry

### **Backend**

A backend/indexing service should:

* Listen to contract events  
* Track bounty states  
* Provide real-time information to the frontend

# **Definition of Done**

The MVP is complete when:

- [ ] A poster can create a bounty  
- [ ] The reward is locked on Stellar Testnet  
- [ ] A contributor can discover a funded bounty  
- [ ] A contributor can claim the bounty  
- [ ] A contributor can submit completed work  
- [ ] A poster can review the submission  
- [ ] Approval triggers payment to the contributor  
- [ ] The transaction is verifiable on Stellar Testnet  
- [ ] Expired bounties can return funds to the poster

# **Final Product Goal**

**ÉréEARN should make bounty payments more trustworthy by connecting completed work directly to verifiable on-chain escrow payments.**

The core experience should be simple:

> **Post a funded bounty. Find opportunities. Complete the work. Get paid on-chain.**